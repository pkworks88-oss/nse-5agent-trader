import argparse
import json
from datetime import datetime, timezone

from core.attribution import compute_desk_and_agent_trust
from core.broker_adapter import BrokerAdapter
from core.charges import compute_net_rr, estimate_roundtrip_charges
from core.config import DEFAULT_DESKS, EARNINGS_BLACKOUT_HOURS, MAX_POSITIONS_PER_SECTOR
from core.ledger import LedgerStore
from core.market_data import get_quote_snapshot
from core.scenario_sim import run_bootstrap_simulation
from core.sizing import compute_position_sizing
from core.stage_audit import build_8_stage_audit


DESK_SAMPLE_CANDIDATES = {
    "SWING": [
        {"symbol": "RELIANCE.NS", "tier": "LOW", "sector": "ENERGY", "entry": 1365.0, "sl": 1335.0, "target": 1425.0, "conviction": 86, "earnings_hours": 480, "lot_size": 1},
    ],
    "INTRADAY": [
        {"symbol": "HDFCBANK.NS", "tier": "MID", "sector": "BANKING", "entry": 830.0, "sl": 822.0, "target": 846.0, "conviction": 84, "earnings_hours": 360, "lot_size": 1},
    ],
    "FNO": [
        {"symbol": "NIFTY 24850 CE", "tier": "HIGH", "sector": "INDEX", "entry": 152.0, "sl": 120.0, "target": 218.0, "conviction": 85, "earnings_hours": 999, "lot_size": 65},
    ],
    "COMMODITY": [
        {"symbol": "GOLDM MCX", "tier": "MID", "sector": "BULLION", "entry": 78300.0, "sl": 78110.0, "target": 78700.0, "conviction": 83, "earnings_hours": 999, "lot_size": 1},
    ],
}


def run_cycle(desk: str = "SWING", max_entries: int = 1) -> dict:
    desk_upper = desk.strip().upper()
    desk_map = {"SWING": "swing", "INTRADAY": "intraday", "FNO": "fno", "COMMODITY": "commodity"}
    desk_key = desk_map[desk_upper]
    cfg = DEFAULT_DESKS[desk_key]

    broker = BrokerAdapter()
    store = LedgerStore()
    state = store.load_all()
    open_trades = [t for t in state["trades"] if t["status"] == "OPEN"]
    desk_open = [t for t in open_trades if str(t.get("desk", "Swing")).lower() == cfg.label.lower()]
    deployed = sum(t["entry"] * t["qty"] for t in desk_open)

    sector_counts = {}
    for t in desk_open:
        sec = t.get("sector", "GENERAL")
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    cycle_id = f"CYC-V48-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    evaluated = 0
    entered = 0
    for cand in DESK_SAMPLE_CANDIDATES.get(desk_upper, []):
        if entered >= max_entries:
            break
        evaluated += 1
        q = get_quote_snapshot(cand["symbol"], default_entry=cand["entry"])
        sizing = compute_position_sizing(
            desk_capital=cfg.capital,
            deployed_capital=deployed,
            entry_price=cand["entry"],
            stop_loss=cand["sl"],
            target_price=cand["target"],
            conviction_score=cand["conviction"],
            max_pos_pct=cfg.max_pos_pct,
            risk_per_trade_pct=cfg.risk_per_trade_pct,
            soft_cushion_pct=cfg.soft_cushion_pct,
            baseline_slots=cfg.baseline_slots,
            current_open_slots=len(desk_open),
            sector=cand["sector"],
            sector_counts=sector_counts,
            max_per_sector=MAX_POSITIONS_PER_SECTOR,
            allow_cushion_tap=cfg.allow_cushion_tap,
            allow_overflow_slots=cfg.allow_overflow_slots,
            lot_size=cand["lot_size"],
        )
        net_rr = compute_net_rr(cand["entry"], cand["sl"], cand["target"], max(1, sizing["qty"]), desk=cfg.label)
        charges = estimate_roundtrip_charges(cand["entry"], cand["target"], max(1, sizing["qty"]), desk=cfg.label)
        sim = run_bootstrap_simulation(cand["entry"], cand["sl"], cand["target"], max(1, sizing["qty"]), charges=charges)
        audit = build_8_stage_audit(
            symbol=cand["symbol"],
            desk=cfg.label,
            tier=cand["tier"],
            entry=cand["entry"],
            sl=cand["sl"],
            target=cand["target"],
            rsi=q["rsi"],
            rvol=q["rvol"],
            pdh=q["pdh"],
            pdl=q["pdl"],
            net_rr=net_rr,
            sector=cand["sector"],
            sector_count=sector_counts.get(cand["sector"], 0),
            earnings_hours=cand["earnings_hours"],
            sizing_result=sizing,
            sim_result=sim,
        )

        scout_sender = "AG1_LOW_RISK" if cand["tier"] == "LOW" else ("AG3_HIGH_RISK" if cand["tier"] == "HIGH" else "AG2_MID_RISK")
        store.append_agent_message({
            "cycle_id": cycle_id,
            "timestamp": now_str,
            "sender": scout_sender,
            "recipient": "AG4_CHIEF_ANALYST",
            "msg_type": "CANDIDATE_PROPOSAL",
            "summary": f"Proposed {cand['symbol']} ({cand['tier']}) @ ₹{cand['entry']} | Desk {cfg.label}",
            "payload_json": json.dumps({"symbol": cand["symbol"], "tier": cand["tier"], "desk": cfg.label, "pdh": q["pdh"], "pdl": q["pdl"]}),
        })

        approved = sizing["approved"] and net_rr >= cfg.min_rr and cand["earnings_hours"] >= EARNINGS_BLACKOUT_HOURS
        store.append_agent_message({
            "cycle_id": cycle_id,
            "timestamp": now_str,
            "sender": "AG4_CHIEF_ANALYST",
            "recipient": "AG5_EXECUTION_BROKER",
            "msg_type": "VERDICT",
            "summary": f"{'Approved' if approved else 'Rejected'} {cand['symbol']} ({sizing['allocation_tag']})",
            "payload_json": json.dumps({"schema": "v4.8", "approved": approved, "audit_8_stage": audit}),
        })

        if approved:
            fill = broker.place_order({"symbol": cand["symbol"], "qty": sizing["qty"], "price": cand["entry"]})
            store.append_trade({
                "timestamp": now_str,
                "symbol": cand["symbol"],
                "tier": cand["tier"],
                "status": "OPEN",
                "qty": sizing["qty"],
                "entry": cand["entry"],
                "sl": cand["sl"],
                "target": cand["target"],
                "exit_price": "",
                "pnl": 0.0,
                "reason": f"V4.8 {cfg.label} ({sizing['allocation_tag']}) | Net R:R 1:{net_rr:.2f}",
                "side": "LONG",
                "initial_sl": cand["sl"],
                "gross_pnl": 0.0,
                "charges_slippage": 0.0,
                "version": "v4.8",
                "desk": cfg.label,
                "allocation_tag": sizing["allocation_tag"],
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "realized_r": 0.0,
            })
            store.append_agent_message({
                "cycle_id": cycle_id,
                "timestamp": now_str,
                "sender": "AG5_EXECUTION_BROKER",
                "recipient": "PORTFOLIO_LEDGER",
                "msg_type": "PAPER_BUY",
                "summary": f"PAPER BOUGHT {sizing['qty']} {cand['symbol']} ({cand['tier']}) @ ₹{cand['entry']}",
                "payload_json": json.dumps(fill),
            })
            entered += 1

    trust = compute_desk_and_agent_trust(store.load_all()["trades"])
    return {
        "version": "v4.8",
        "live_mode": broker.live_mode,
        "ledger_source": state["source"],
        "desk": desk_upper,
        "evaluated": evaluated,
        "entered": entered,
        "batch_attribution": trust,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Multi-Asset V4.8 Headless Paper Cycle Runner")
    parser.add_argument(
        "--desk",
        default="SWING",
        type=lambda s: s.strip().upper(),
        choices=["SWING", "INTRADAY", "FNO", "COMMODITY"],
    )
    parser.add_argument("--max-entries", type=int, default=1)
    args = parser.parse_args()
    result = run_cycle(desk=args.desk, max_entries=args.max_entries)
    print(json.dumps(result, indent=2))
