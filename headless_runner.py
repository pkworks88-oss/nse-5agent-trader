import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.attribution import compute_desk_and_agent_trust
from core.broker_adapter import BrokerAdapter
from core.charges import compute_net_rr, estimate_roundtrip_charges
from core.config import (normalize_desk_key, DEFENSIVE_ALLOWED_TIERS, DEFENSIVE_RISK_MULT, EARNINGS_BLACKOUT_HOURS, FNO_MIN_PREMIUM, MAX_POSITIONS_PER_SECTOR,
                         MIN_CONVICTION, get_desk_config, get_effective_desk_config)
from core.intelligence import evaluate_with_gemini_if_configured, fetch_news_sentiment, get_desk_candidates
from core.ledger import LedgerStore
from core.market_data import STALE_FEEDS, get_live_quotes_batch, get_nse_session_status, get_quote_snapshot, get_swing_regime
from core.scenario_sim import run_bootstrap_simulation
from core.sizing import compute_position_sizing
from core.stage_audit import build_8_stage_audit
from core.watchdog import _compute_stepped_profit_floor, evaluate_watchdog_positions

DESK_CADENCE_SECONDS: Dict[str, int] = {"SWING": 900, "INTRADAY": 300, "FNO": 300, "COMMODITY": 300}


def _today_ist_date_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _effective_peak(accounts: Dict[str, Any]) -> float:
    """The stored combined peak only counts on the IST day it was recorded; a new day starts at 0."""
    if str(accounts.get("peak_date", "") or "").strip() != _today_ist_date_str():
        return 0.0
    return float(accounts.get("combined_peak_pnl", 0.0) or 0.0)


def _compute_today_realized_pnl(trades: List[Dict[str, Any]], desk_filter: Optional[List[str]] = None) -> float:
    today_str = _today_ist_date_str()
    total = 0.0
    for t in trades:
        if str(t.get("status", "")).upper() == "OPEN":
            continue
        if desk_filter:
            d_low = normalize_desk_key(t.get("desk", ""))
            if d_low not in desk_filter:
                continue
        ts_check = str(t.get("exit_timestamp") or t.get("timestamp") or "")
        if ts_check.startswith(today_str):
            total += float(t.get("pnl", 0.0) or 0.0)
    return round(total, 2)


def _swing_day_pnl(all_trades: List[Dict[str, Any]], desk_open: List[Dict[str, Any]]) -> float:
    """Swing 'daily' P&L = today's move of the open book (vs previous close; vs entry if opened today)
    + P&L of swing trades opened AND closed today. (Total unrealized since entry is not a *daily* number.)"""
    today = _today_ist_date_str()
    total = 0.0
    syms = [str(t.get("symbol", "")).strip().upper() for t in desk_open]
    quotes = get_live_quotes_batch(syms, force_live=True) if syms else {}
    for t in desk_open:
        sym = str(t.get("symbol", "")).strip().upper()
        q = quotes.get(sym, {})
        if str(q.get("feed", "")) in STALE_FEEDS:
            continue
        entry = float(t.get("entry", 0.0) or 0.0)
        ref = entry if str(t.get("timestamp", "")).startswith(today) else float(q.get("prev_close", entry) or entry)
        total += (float(q.get("ltp", ref) or ref) - ref) * int(float(t.get("qty", 0) or 0))
    for t in all_trades:
        if (str(t.get("status", "")).upper() != "OPEN" and str(t.get("desk", "")).strip().lower() == "swing"
                and str(t.get("timestamp", "")).startswith(today) and str(t.get("exit_timestamp", "")).startswith(today)):
            total += float(t.get("pnl", 0.0) or 0.0)
    return round(total, 2)


def run_watchdog_pass(store: LedgerStore, state: Dict[str, Any], dry_run: bool = False, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    all_trades = state.get("trades", [])
    open_trades = [t for t in all_trades if str(t.get("status", "")).upper() == "OPEN"]
    open_syms = [str(t.get("symbol", "")).strip().upper() for t in open_trades if t.get("symbol")]
    default_entries = {str(t.get("symbol", "")).strip().upper(): float(t.get("entry", 100.0) or 100.0) for t in open_trades if t.get("symbol")}
    quotes_map = get_live_quotes_batch(open_syms, default_entries=default_entries, force_live=True) if open_syms else {}

    # Never run SL/target logic on made-up prices. At EOD we still square off (positions must not be held overnight).
    sess_now = get_nse_session_status(now_utc)
    stale_syms = {s for s, q in quotes_map.items() if str(q.get("feed", "")) in STALE_FEEDS}
    eval_trades = open_trades if sess_now["is_eod_squareoff"] else [t for t in open_trades if str(t.get("symbol", "")).strip().upper() not in stale_syms]

    accounts = state.get("accounts", {})
    desk_cfgs = {k: get_effective_desk_config(k, accounts) for k in ("swing", "intraday", "fno", "commodity")}
    realized_by_desk = {k: _compute_today_realized_pnl(all_trades, desk_filter=[k]) for k in desk_cfgs}
    persisted_peak = _effective_peak(accounts)

    wd_res = evaluate_watchdog_positions(
        eval_trades, quotes_map, dry_run=dry_run, now_utc=now_utc,
        today_realized_by_desk=realized_by_desk, persisted_peak_pnl=persisted_peak,
        swing_peak_pnl=float(accounts.get("swing_peak_pnl", 0.0) or 0.0), desk_configs=desk_cfgs,
    )

    wd_res["skipped_stale_feed"] = sorted(stale_syms) if not sess_now["is_eod_squareoff"] else []

    if not dry_run:
        store.sync_watchdog_trades(wd_res.get("trades", []), combined_peak_pnl=wd_res.get("combined_peak_pnl"), locked_floor=wd_res.get("locked_floor"), peak_date=_today_ist_date_str(), swing_peak_pnl=wd_res.get("swing_peak_pnl"))
        if wd_res["actions"]:
            ist_dt = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            cycle_id = f"WD-20S-{ist_dt.strftime('%Y%m%d%H%M%S')}"
            for act in wd_res["actions"]:
                store.append_agent_message({
                    "cycle_id": cycle_id, "timestamp": act["timestamp"],
                    "sender": "AG5_EXECUTION_BROKER", "recipient": f"{str(act['desk']).upper()}_LEDGER",
                    "msg_type": act.get("status") or act["action"],
                    "summary": f"{act['symbol']} ({act['desk']}): {act['reason']}",
                    "payload_json": json.dumps(act),
                }, desk=str(act["desk"]))
    return wd_res


def run_cycle(desk: str = "SWING", max_entries: int = 1, ignore_session_gate: bool = False, cas_enabled: Optional[bool] = None) -> Dict[str, Any]:
    desk_upper = normalize_desk_key(desk).upper()
    cfg = get_desk_config(desk_upper)

    broker = BrokerAdapter()
    store = LedgerStore()
    state = store.load_all()
    wd_summary = run_watchdog_pass(store, state, dry_run=False)
    state = store.load_all()
    cfg = get_effective_desk_config(desk_upper, state.get("accounts", {}))     # dashboard settings reach the runner here

    ist_dt = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    cycle_id = f"CYC-V48-{ist_dt.strftime('%Y%m%d%H%M%S')}"
    now_str = ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")

    cas_active = cas_enabled if cas_enabled is not None else (os.getenv("CAS_ENABLED", "true").strip().lower() == "true")
    if not cas_active:
        msg = f"{cfg.label} cycle skipped: CAS (Capital Allocation Switch) is OFF."
        store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": f"{desk_upper}_LEDGER", "msg_type": "GATE_BLOCKED", "summary": msg, "payload_json": json.dumps({"gate": "CAS_OFF"})}, desk=cfg.label)
        return {"version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300), "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"], "desk": desk_upper, "gate_blocked": "CAS_OFF", "evaluated": 0, "entered": 0, "watchdog_summary": wd_summary}

    sess = get_nse_session_status()
    if not ignore_session_gate and not sess["can_open_new_entries"]:
        msg = f"{cfg.label} new entries blocked by session gate: {sess['state']} ({sess['time_ist']})."
        store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": f"{desk_upper}_LEDGER", "msg_type": "SESSION_GATE_BLOCKED", "summary": msg, "payload_json": json.dumps({"gate": "SESSION_OR_1400_CUTOFF", "session": sess})}, desk=cfg.label)
        return {"version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300), "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"], "desk": desk_upper, "gate_blocked": sess["state"], "evaluated": 0, "entered": 0, "watchdog_summary": wd_summary}

    all_trades = state["trades"]
    open_trades = [t for t in all_trades if str(t.get("status", "")).upper() == "OPEN"]
    open_symbols = {str(t.get("symbol", "")).strip().upper() for t in open_trades}
    desk_open = [t for t in open_trades if normalize_desk_key(t.get("desk", "Swing")) == cfg.key]
    deployed = sum(float(t.get("entry", 0.0) or 0.0) * int(float(t.get("qty", 0) or 0)) * cfg.margin_pct for t in desk_open)
    desk_closed_pnl = sum(float(t.get("pnl", 0.0) or 0.0) for t in all_trades
                          if str(t.get("status", "")).upper() != "OPEN" and normalize_desk_key(t.get("desk", "Swing")) == cfg.key)
    eff_capital = max(0.25 * cfg.capital, cfg.capital + desk_closed_pnl) if cfg.capital > 0 else 0.0   # P&L compounds into sizing

    desk_today_realized = _compute_today_realized_pnl(all_trades, desk_filter=[cfg.key])
    desk_open_mtm = sum(float(t.get("mtm_pnl", 0.0) or 0.0) for t in desk_open)
    desk_daily_pnl = round(desk_today_realized + desk_open_mtm, 2)
    if cfg.key == "swing":
        desk_daily_pnl = _swing_day_pnl(all_trades, desk_open)
    if desk_daily_pnl <= cfg.daily_loss_limit:
        msg = f"{cfg.label} circuit breaker tripped: daily P&L ₹{desk_daily_pnl:+,.2f} <= limit ₹{cfg.daily_loss_limit:,.2f}."
        store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": f"{desk_upper}_LEDGER", "msg_type": "CIRCUIT_BREAKER_TRIPPED", "summary": msg, "payload_json": json.dumps({"gate": "DAILY_LOSS_LIMIT", "daily_pnl": desk_daily_pnl, "limit": cfg.daily_loss_limit})}, desk=cfg.label)
        return {"version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300), "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"], "desk": desk_upper, "gate_blocked": "DAILY_LOSS_CIRCUIT_BREAKER", "evaluated": 0, "entered": 0, "watchdog_summary": wd_summary}

    if cfg.key in ("intraday", "fno"):
        combined_open = [t for t in open_trades if normalize_desk_key(t.get("desk", "")) in ("intraday", "fno")]
        combined_pnl = _compute_today_realized_pnl(all_trades, desk_filter=["intraday", "fno"]) + sum(float(t.get("mtm_pnl", 0.0) or 0.0) for t in combined_open)
        peak_today = max(_effective_peak(state.get("accounts", {})), combined_pnl, 0.0)
        floor_now = _compute_stepped_profit_floor(peak_today)
        if floor_now > 0 and combined_pnl < floor_now:
            msg = f"{cfg.label} entries halted: combined Intraday+F&O P&L ₹{combined_pnl:+,.2f} is below locked floor ₹{floor_now:,.2f} (peak ₹{peak_today:,.2f})."
            store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": f"{desk_upper}_LEDGER", "msg_type": "PROFIT_FLOOR_LOCKED", "summary": msg, "payload_json": json.dumps({"gate": "PROFIT_FLOOR", "pnl": combined_pnl, "floor": floor_now, "peak": peak_today})}, desk=cfg.label)
            return {"version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300), "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"], "desk": desk_upper, "gate_blocked": "PROFIT_FLOOR_LOCKED", "evaluated": 0, "entered": 0, "watchdog_summary": wd_summary}

    regime: Optional[Dict[str, Any]] = None
    if cfg.key == "swing":
        regime = get_swing_regime()
        store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": "SWING_LEDGER", "msg_type": "MARKET_REGIME",
                                    "summary": f"Swing regime {regime['mode']}" + (f": {'; '.join(regime['reasons'])}" if regime["reasons"] else " (Nifty above 20-SMA, no stress)"),
                                    "payload_json": json.dumps(regime)}, desk=cfg.label)
        if regime["mode"] == "UNKNOWN":
            return {"version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300), "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"], "desk": desk_upper, "gate_blocked": "REGIME_DATA_UNAVAILABLE", "evaluated": 0, "entered": 0, "watchdog_summary": wd_summary}

    sector_counts: Dict[str, int] = {}
    for t in desk_open:
        sec = str(t.get("sector", "GENERAL")).strip().upper() or "GENERAL"
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    evaluated = 0
    entered = 0
    for cand in get_desk_candidates(desk_upper):
        if entered >= max_entries:
            break
        sym = str(cand["symbol"]).strip().upper()
        if sym in open_symbols:
            continue
        evaluated += 1
        q = get_quote_snapshot(sym, default_entry=cand["entry"])
        feed_tag = str(cand.get("feed") or q.get("feed") or "")
        if feed_tag in STALE_FEEDS and os.getenv("ALLOW_FALLBACK_FEED", "false").strip().lower() != "true":
            store.append_agent_message({"cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": "AG5_EXECUTION_BROKER", "msg_type": "VERDICT", "summary": f"Rejected {sym} ({cfg.label}) — no live market data (feed={feed_tag}).", "payload_json": json.dumps({"approved": False, "reasons": ["STALE_FEED"], "feed": feed_tag})}, desk=cfg.label)
            continue
        pre_reject: List[str] = []
        risk_pct_eff = cfg.risk_per_trade_pct
        if regime is not None and regime["defensive"]:
            if str(cand["tier"]).upper() not in DEFENSIVE_ALLOWED_TIERS:
                pre_reject.append(f"Defensive regime: only {'/'.join(DEFENSIVE_ALLOWED_TIERS)} tier allowed")
            risk_pct_eff = cfg.risk_per_trade_pct * DEFENSIVE_RISK_MULT
        if cfg.key == "fno" and float(cand["entry"]) < FNO_MIN_PREMIUM:
            pre_reject.append(f"Premium ₹{cand['entry']} < minimum ₹{FNO_MIN_PREMIUM:.0f}")
        news = fetch_news_sentiment(sym, limit=3)
        ai_eval = evaluate_with_gemini_if_configured(cand, q, news)
        effective_news_status = ai_eval["news_status"]
        adj_conviction = max(0, min(100, int(cand["conviction"]) + int(ai_eval["conviction_adj"])))

        sizing = compute_position_sizing(
            desk_capital=eff_capital, deployed_capital=deployed, entry_price=cand["entry"],
            stop_loss=cand["sl"], target_price=cand["target"], conviction_score=adj_conviction,
            max_pos_pct=cfg.max_pos_pct, risk_per_trade_pct=risk_pct_eff,
            soft_cushion_pct=cfg.soft_cushion_pct, baseline_slots=cfg.baseline_slots,
            current_open_slots=len(desk_open), sector=cand["sector"], sector_counts=sector_counts,
            max_per_sector=MAX_POSITIONS_PER_SECTOR, allow_cushion_tap=cfg.allow_cushion_tap,
            allow_overflow_slots=cfg.allow_overflow_slots, lot_size=cand["lot_size"],
            atr=cand.get("atr"), beta=cand.get("beta"), max_overflow_slots=cfg.max_overflow_slots, margin_pct=cfg.margin_pct,
        )
        net_rr = compute_net_rr(cand["entry"], cand["sl"], cand["target"], max(1, sizing["qty"]), desk=cfg.label)
        charges = estimate_roundtrip_charges(cand["entry"], cand["target"], max(1, sizing["qty"]), desk=cfg.label)
        sim = run_bootstrap_simulation(cand["entry"], cand["sl"], cand["target"], max(1, sizing["qty"]), charges=charges)
        audit = build_8_stage_audit(
            symbol=sym, desk=cfg.label, tier=cand["tier"], entry=cand["entry"], sl=cand["sl"], target=cand["target"],
            rsi=float(cand.get("rsi", q["rsi"])), rvol=float(cand.get("rvol", q["rvol"])),
            pdh=float(cand.get("pdh", q["pdh"])), pdl=float(cand.get("pdl", q["pdl"])),
            net_rr=net_rr, sector=cand["sector"], sector_count=sector_counts.get(cand["sector"], 0),
            earnings_hours=cand["earnings_hours"], sizing_result=sizing, sim_result=sim,
        )

        stage7_approved = audit[6]["status"] == "APPROVED"
        reject_reasons: List[str] = list(pre_reject)
        if not stage7_approved:
            reject_reasons.append(audit[6]["why"])
        if effective_news_status == "NEGATIVE_RISK":
            reject_reasons.append("Negative news risk")
        if adj_conviction < MIN_CONVICTION:
            reject_reasons.append(f"Conviction {adj_conviction} < {MIN_CONVICTION}")

        approved = len(reject_reasons) == 0
        scout_sender = "AG1_LOW_RISK" if cand["tier"] == "LOW" else ("AG3_HIGH_RISK" if cand["tier"] == "HIGH" else "AG2_MID_RISK")
        store.append_agent_message({
            "cycle_id": cycle_id, "timestamp": now_str, "sender": scout_sender, "recipient": "AG4_CHIEF_ANALYST",
            "msg_type": "CANDIDATE_PROPOSAL",
            "summary": f"Proposed {sym} ({cand['tier']}) @ ₹{cand['entry']} | RSI {cand.get('rsi', q['rsi']):.1f} | RVOL {cand.get('rvol', q['rvol']):.2f}x | News {effective_news_status}",
            "payload_json": json.dumps({"symbol": sym, "tier": cand["tier"], "desk": cfg.label, "news": effective_news_status, "ai": ai_eval}),
        }, desk=cfg.label)

        verdict_detail = f"Net R:R 1:{net_rr:.2f}, Conv {adj_conviction}" if approved else "; ".join(reject_reasons)
        store.append_agent_message({
            "cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": "AG5_EXECUTION_BROKER",
            "msg_type": "VERDICT",
            "summary": f"{'Approved' if approved else 'Rejected'} {sym} ({cfg.label}) — {verdict_detail}",
            "payload_json": json.dumps({"schema": "v4.8", "approved": approved, "reasons": reject_reasons, "audit_8_stage": audit}),
        }, desk=cfg.label)

        if approved:
            fill = broker.place_order({"symbol": sym, "qty": sizing["qty"], "price": cand["entry"]})
            lot_sz = max(1, int(cand["lot_size"]))
            qty_lots = max(1, int(sizing["qty"]) // lot_sz)
            store.append_trade({
                "timestamp": now_str, "exit_timestamp": "", "symbol": sym, "sector": cand["sector"],
                "tier": cand["tier"], "strategy": cand["tier"], "status": "OPEN",
                "qty": sizing["qty"], "qty_or_lots": qty_lots, "lot_size": lot_sz,
                "entry": cand["entry"], "initial_sl": cand["sl"], "sl": cand["sl"], "target": cand["target"], "initial_target": cand["target"],
                "exit_price": "", "gross_pnl": 0.0, "charges_slippage": 0.0, "pnl": 0.0,
                "realized_r": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "version": "v4.8",
                "desk": cfg.label, "allocation_tag": sizing["allocation_tag"],
                "reason": f"V4.8 {cfg.label} ({sizing['allocation_tag']}) | Net R:R 1:{net_rr:.2f} | {cand.get('rationale', '')}",
                "side": "LONG",
            })
            store.append_agent_message({
                "cycle_id": cycle_id, "timestamp": now_str, "sender": "AG5_EXECUTION_BROKER", "recipient": f"{desk_upper}_LEDGER",
                "msg_type": "PAPER_BUY" if not broker.live_mode else "LIVE_BUY",
                "summary": f"{'BOUGHT' if not broker.live_mode else 'LIVE BOUGHT'} {sizing['qty']} {sym} ({cand['tier']}) @ ₹{cand['entry']} | Initial SL ₹{cand['sl']}",
                "payload_json": json.dumps(fill),
            }, desk=cfg.label)
            entered += 1
            open_symbols.add(sym)
            desk_open.append({"symbol": sym, "entry": cand["entry"], "qty": sizing["qty"]})
            deployed += sizing.get("cash_required", sizing["notional"])
            sector_counts[cand["sector"]] = sector_counts.get(cand["sector"], 0) + 1

    store.append_agent_message({
        "cycle_id": cycle_id, "timestamp": now_str, "sender": "AG4_CHIEF_ANALYST", "recipient": f"{desk_upper}_LEDGER",
        "msg_type": "CYCLE_SCAN",
        "summary": f"{cfg.label} scan completed: evaluated {evaluated}, entered {entered} (Paper Mode).",
        "payload_json": json.dumps({"desk": desk_upper, "evaluated": evaluated, "entered": entered}),
    }, desk=cfg.label)
    trust = compute_desk_and_agent_trust(store.load_all()["trades"])
    return {
        "version": "v4.8", "cadence_seconds": DESK_CADENCE_SECONDS.get(desk_upper, 300),
        "watchdog_seconds": 20, "live_mode": broker.live_mode, "ledger_source": state["source"],
        "desk": desk_upper, "evaluated": evaluated, "entered": entered,
        "watchdog_summary": wd_summary, "batch_attribution": trust,
    }


def run_watchdog_loop(watchdog_seconds: int = 20, iterations: int = 1) -> Dict[str, Any]:
    store = LedgerStore()
    results: List[Dict[str, Any]] = []
    for idx in range(max(1, iterations)):
        state = store.load_all()
        res = run_watchdog_pass(store, state, dry_run=False)
        results.append(res)
        if idx < iterations - 1 and watchdog_seconds > 0:
            time.sleep(watchdog_seconds)
    return {"version": "v4.8", "mode": "20s_watchdog_loop", "watchdog_seconds": watchdog_seconds, "iterations_completed": len(results), "runs": results}


def trigger_manual_agent_cycle(desk: str = "ALL", max_entries: int = 1, watchdog_bursts: int = 1, ignore_session_gate: bool = True, cas_enabled: bool = True) -> Dict[str, Any]:
    d_up = "ALL" if str(desk or "ALL").strip().upper() == "ALL" else normalize_desk_key(desk).upper()
    desks_to_run = ["SWING", "INTRADAY", "FNO", "COMMODITY"] if d_up == "ALL" else [d_up]
    cycle_results = [run_cycle(desk=d, max_entries=max_entries, ignore_session_gate=ignore_session_gate, cas_enabled=cas_enabled) for d in desks_to_run]
    wd_res = run_watchdog_loop(watchdog_seconds=0, iterations=max(1, int(watchdog_bursts)))
    return {"manual_run": True, "requested_desk": d_up, "cycles": cycle_results, "watchdog": wd_res}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Multi-Asset V4.8 Automated Cycle & 20s Watchdog Runner")
    parser.add_argument("--desk", default="SWING", type=lambda s: normalize_desk_key(s).upper(), choices=["SWING", "INTRADAY", "FNO", "COMMODITY"])
    parser.add_argument("--max-entries", type=int, default=1)
    parser.add_argument("--watchdog-only", action="store_true")
    parser.add_argument("--watchdog-seconds", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--force-session", action="store_true")
    args = parser.parse_args()
    output = run_watchdog_loop(watchdog_seconds=args.watchdog_seconds, iterations=args.iterations) if args.watchdog_only else run_cycle(desk=args.desk, max_entries=args.max_entries, ignore_session_gate=args.force_session)
    print(json.dumps(output, indent=2))
