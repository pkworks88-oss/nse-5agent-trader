from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.charges import estimate_roundtrip_charges
from core.config import (
    INTRADAY_FLOOR_STEPS, SWING_FLOOR_STEPS, SWING_ROLLOVER_BUFFER, SWING_ROLLOVER_EXT,
    SWING_TIME_STOP_DAYS, get_desk_config, normalize_desk_key,
)
from core.market_data import STALE_FEEDS, count_trading_days
from core.trailing import compute_trailing_sl

SHORT_TERM_DESKS = ("intraday", "fno", "commodity")


def _norm_desk(label: str) -> str:
    return normalize_desk_key(label)


def _compute_stepped_profit_floor(peak_pnl: float, steps: Optional[list] = None) -> float:
    """Floor for a peak P&L. A step value in (0,1) means 'lock that fraction of the peak'."""
    floor_amt = 0.0
    for threshold, floor_val in sorted(steps or INTRADAY_FLOOR_STEPS, key=lambda x: x[0]):
        if peak_pnl >= threshold:
            floor_amt = max(floor_amt, peak_pnl * floor_val if (0.0 < floor_val < 1.0) else float(floor_val))
    return round(floor_amt, 2)


def _finalize_exit(tr: Dict[str, Any], status: str, fill_px: float, now_ist_str: str) -> Dict[str, Any]:
    """Write exit fields onto the trade dict and return the P&L numbers."""
    side = str(tr.get("side", "LONG")).strip().upper() or "LONG"
    entry = float(tr.get("entry", 0.0) or 0.0)
    qty = int(float(tr.get("qty", 0) or 0))
    init_sl = float(tr.get("initial_sl", 0.0) or 0.0) or float(tr.get("sl", 0.0) or 0.0)
    desk_label = str(tr.get("desk", "Swing")).strip()
    direction = -1.0 if side == "SHORT" else 1.0
    fixed_risk_unit = max(0.05, abs(entry - init_sl))
    gross = round((fill_px - entry) * qty * direction, 2)
    charges = estimate_roundtrip_charges(entry, fill_px, qty, desk=desk_label)
    net = round(gross - charges, 2)
    r_mult = round(((fill_px - entry) * direction) / fixed_risk_unit, 2)
    orig = str(tr.get("reason", "")).strip()
    tr.update({
        "status": status, "exit_timestamp": now_ist_str, "exit_price": round(fill_px, 2), "gross_pnl": gross,
        "charges_slippage": charges, "pnl": net, "realized_r": r_mult,
        "reason": f"{orig} [{status} @ ₹{fill_px:.2f} | Net ₹{net:+,.2f} ({r_mult:+.2f}R)]".strip(),
    })
    return {"net_pnl": net, "realized_r": r_mult, "gross_pnl": gross}


def _entry_date(tr: Dict[str, Any]) -> Optional[date]:
    try:
        return datetime.strptime(str(tr.get("timestamp", ""))[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def evaluate_watchdog_positions(
    open_trades: List[Dict[str, Any]],
    quotes_by_symbol: Dict[str, Dict[str, float]],
    dry_run: bool = True,
    now_utc: Optional[datetime] = None,
    today_realized_intraday_fno: float = 0.0,
    persisted_peak_pnl: float = 0.0,
    today_realized_by_desk: Optional[Dict[str, float]] = None,
    swing_peak_pnl: float = 0.0,
    desk_configs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_dt = now_utc or datetime.now(timezone.utc)
    dt_utc = raw_dt.astimezone(timezone.utc) if raw_dt.tzinfo else raw_dt.replace(tzinfo=timezone.utc)
    dt_ist = dt_utc + timedelta(hours=5, minutes=30)
    now_ist_str = dt_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    mins_ist = dt_ist.hour * 60 + dt_ist.minute
    is_eod = (dt_ist.weekday() < 5) and (mins_ist >= (15 * 60 + 15))
    today_ist = dt_ist.date()

    def cfg_for(label: str):
        key = _norm_desk(label)
        return (desk_configs or {}).get(key) or get_desk_config(label)

    actions: List[Dict[str, Any]] = []
    trades_out: List[Dict[str, Any]] = []
    still_open: Dict[int, str] = {}       # index in trades_out -> desk key

    for t in open_trades:
        tr = dict(t)
        tr.setdefault("status", "OPEN")
        desk_label = str(tr.get("desk", "Swing")).strip()
        desk_key = _norm_desk(desk_label)
        cfg = cfg_for(desk_label)
        sym = str(tr.get("symbol", "")).strip().upper()
        side = str(tr.get("side", "LONG")).strip().upper() or "LONG"
        entry = float(tr.get("entry", 0.0) or 0.0)
        sl = float(tr.get("sl", 0.0) or 0.0)
        init_sl = float(tr.get("initial_sl", 0.0) or 0.0)
        if init_sl <= 0 or (side == "LONG" and init_sl > entry and sl < entry):
            init_sl = sl if (side == "LONG" and sl < entry) else round(entry * 0.985, 2)
        tr["initial_sl"] = init_sl
        target = float(tr.get("target", 0.0) or 0.0)
        initial_target = float(tr.get("initial_target", 0.0) or 0.0) or target
        tr["initial_target"] = initial_target
        qty = int(float(tr.get("qty", 0) or 0))
        q = quotes_by_symbol.get(sym, {})
        ltp = float(q.get("ltp", entry) or entry)
        bar_high = float(q.get("bar_high", ltp) or ltp)
        bar_low = float(q.get("bar_low", ltp) or ltp)
        stale_quote = str(q.get("feed", "")) in STALE_FEEDS

        if entry <= 0 or qty <= 0:
            trades_out.append(tr)
            continue

        fixed_risk_unit = max(0.05, abs(entry - init_sl))
        reward_dist = max(0.05, abs(target - entry)) if target > 0 else fixed_risk_unit * 1.6
        if side == "SHORT":
            favorable_px, adverse_px = min(ltp, bar_low), max(ltp, bar_high)
            prog_pct = ((entry - ltp) / reward_dist) * 100.0
            mtm_gross = (entry - ltp) * qty
        else:
            favorable_px, adverse_px = max(ltp, bar_high), min(ltp, bar_low)
            prog_pct = ((ltp - entry) / reward_dist) * 100.0
            mtm_gross = (ltp - entry) * qty

        fav_move = ((favorable_px - entry) / entry) * 100.0 if side == "LONG" else ((entry - favorable_px) / entry) * 100.0
        adv_move = ((adverse_px - entry) / entry) * 100.0 if side == "LONG" else ((entry - adverse_px) / entry) * 100.0
        tr["mfe_pct"] = max(float(tr.get("mfe_pct", 0.0) or 0.0), round(fav_move, 2))
        tr["mae_pct"] = min(float(tr.get("mae_pct", 0.0) or 0.0), round(adv_move, 2))
        tr["ltp"] = round(ltp, 2)
        tr["mtm_gross"] = round(mtm_gross, 2)
        tr["mtm_pnl"] = round(mtm_gross - estimate_roundtrip_charges(entry, ltp, qty, desk=desk_label), 2)   # NET

        new_sl, ratcheted, rolled_now = sl, False, False
        target_span = abs(target - entry) if target > 0 else reward_dist

        # 1) Test the stop/target that were ALREADY in force (a stop raised this tick can't be hit by an earlier wick).
        exit_status, fill_px = None, ltp
        if side == "SHORT":
            if sl > 0 and adverse_px >= sl:
                exit_status, fill_px = ("TRAILING_SL_HIT" if sl < entry else "SL_HIT"), round(max(ltp, sl), 2)
            elif target > 0 and favorable_px <= target:
                exit_status, fill_px = "TARGET_HIT", round(min(ltp, target), 2)
        else:
            if sl > 0 and adverse_px <= sl:
                exit_status, fill_px = ("TRAILING_SL_HIT" if sl > entry else "SL_HIT"), round(min(ltp, sl), 2)
            elif target > 0 and favorable_px >= target:
                exit_status, fill_px = "TARGET_HIT", round(max(ltp, target), 2)

        # 1b) Swing Target-1 rollover: don't sell at T1; lock ~T1, aim for T2 = T1 + 0.5*(T1-entry). One roll only.
        if (exit_status == "TARGET_HIT" and desk_key == "swing" and side == "LONG"
                and target <= initial_target + 0.005 and not stale_quote):
            t1 = target
            new_target = round(t1 + SWING_ROLLOVER_EXT * (t1 - entry), 2)
            roll_sl = round(max(sl, t1 * (1.0 - SWING_ROLLOVER_BUFFER)), 2)
            tr["target"], tr["sl"], tr["initial_target"] = new_target, roll_sl, initial_target
            new_sl, rolled_now = roll_sl, True
            target = new_target
            exit_status, fill_px = None, ltp
            if favorable_px >= new_target:                      # gapped straight through T2
                exit_status, fill_px = "TARGET_HIT", round(max(ltp, new_target), 2)
            actions.append({
                "timestamp": now_ist_str, "symbol": sym, "desk": desk_label, "action": "TARGET_ROLLOVER", "status": "OPEN",
                "reason": f"T1 ₹{t1:.2f} reached: SL -> ₹{roll_sl:.2f} (locks ~{(roll_sl - entry) / max(t1 - entry, 0.01) * 100:.0f}% of T1), Target 2 -> ₹{new_target:.2f}",
                "ltp": ltp, "sl": roll_sl, "dry_run": dry_run,
            })

        # 2) Only if still open and not just rolled: ratchet (25% breakeven, then +10% steps).
        if exit_status is None and not rolled_now:
            est_rt = estimate_roundtrip_charges(entry, max(ltp, entry), max(1, qty), desk=desk_label)
            cand = compute_trailing_sl(side, entry, sl, target_span, ltp, prog_pct, float(cfg.trail_be_pct), float(cfg.trail_step_pct), est_rt / max(1, qty))
            if cand is not None:
                new_sl, ratcheted = cand, True
                tr["sl"] = new_sl

        # 3) Swing dead-money time stop (evaluated in the EOD window, never on a made-up price)
        if exit_status is None and desk_key == "swing" and is_eod and not stale_quote:
            ed = _entry_date(tr)
            protected = (new_sl > entry) if side == "LONG" else (0 < new_sl < entry)
            if ed is not None and count_trading_days(ed, today_ist) >= SWING_TIME_STOP_DAYS and not protected:
                exit_status, fill_px = "TIME_STOP", round(ltp, 2)

        if exit_status:
            res = _finalize_exit(tr, exit_status, fill_px, now_ist_str)
            actions.append({
                "timestamp": now_ist_str, "symbol": sym, "desk": desk_label, "action": "EXIT", "status": exit_status,
                "reason": f"{exit_status} @ ₹{fill_px:.2f} (Net ₹{res['net_pnl']:+,.2f}, {res['realized_r']:+.2f}R)",
                "ltp": fill_px, "sl": new_sl, "net_pnl": res["net_pnl"], "realized_r": res["realized_r"], "dry_run": dry_run,
            })
        elif ratcheted:
            locked_amt = round(max(0.0, ((new_sl - entry) if side == "LONG" else (entry - new_sl)) * qty), 2)
            actions.append({
                "timestamp": now_ist_str, "symbol": sym, "desk": desk_label, "action": "TRAIL_RATCHET", "status": "OPEN",
                "reason": f"Ratcheted SL ₹{sl:.2f} -> ₹{new_sl:.2f} (Prog {prog_pct:+.1f}%, Locked ₹{locked_amt:,.0f})",
                "ltp": ltp, "sl": new_sl, "locked_profit": locked_amt, "dry_run": dry_run,
            })

        trades_out.append(tr)
        if tr.get("status") == "OPEN":
            still_open[len(trades_out) - 1] = desk_key

    # ---------------- portfolio-level protections (net P&L everywhere) ----------------
    realized_by_desk = dict(today_realized_by_desk or {})
    if "intraday" not in realized_by_desk and "fno" not in realized_by_desk:
        realized_by_desk["intraday"] = float(today_realized_intraday_fno)   # legacy single-number input
    open_net: Dict[str, float] = {}
    for i, dk in still_open.items():
        open_net[dk] = open_net.get(dk, 0.0) + float(trades_out[i].get("mtm_pnl", 0.0) or 0.0)

    close_plan: Dict[int, str] = {}

    # (a) Intraday + F&O combined stepped floor (3k -> 1.5k, 4k -> 3k, 5k+ -> 75% of peak), resets daily upstream
    intra_idx = [i for i, dk in still_open.items() if dk in ("intraday", "fno")]
    combined = round(float(realized_by_desk.get("intraday", 0.0)) + float(realized_by_desk.get("fno", 0.0)) + open_net.get("intraday", 0.0) + open_net.get("fno", 0.0), 2)
    new_peak = round(max(float(persisted_peak_pnl), combined, 0.0), 2)
    active_floor = _compute_stepped_profit_floor(new_peak)
    if active_floor > 0.0 and combined < active_floor:
        for i in intra_idx:
            close_plan[i] = "PROFIT_FLOOR_SQUAREOFF"

    # (b) Swing open-basket stepped floor (7k -> 3.5k, 10k -> 6.5k, 15k -> 10.5k, 20k+ -> 75% of peak)
    swing_idx = [i for i, dk in still_open.items() if dk == "swing"]
    swing_basket = round(open_net.get("swing", 0.0), 2)
    new_swing_peak = round(max(float(swing_peak_pnl), swing_basket, 0.0), 2) if swing_idx else 0.0
    swing_floor = _compute_stepped_profit_floor(new_swing_peak, SWING_FLOOR_STEPS)
    if swing_idx and cfg_for("swing").basket_floor and swing_floor > 0.0 and swing_basket < swing_floor:
        for i in swing_idx:
            close_plan[i] = "PROFIT_FLOOR_SQUAREOFF"

    # (c) Per-desk daily-loss circuit breaker -> liquidate (desks with breaker_liquidates=True)
    breaker_tripped: List[str] = []
    for dk in sorted(set(still_open.values())):
        cfg = cfg_for(dk)
        if not cfg.breaker_liquidates or cfg.daily_loss_limit >= 0:
            continue
        desk_pnl = float(realized_by_desk.get(dk, 0.0)) + open_net.get(dk, 0.0)
        if desk_pnl <= cfg.daily_loss_limit:
            breaker_tripped.append(dk)
            for i, d2 in still_open.items():
                if d2 == dk:
                    close_plan[i] = "CIRCUIT_BREAKER_SQUAREOFF"

    # (d) EOD square-off of all short-term desks (overrides everything)
    if is_eod:
        for i, dk in still_open.items():
            if dk in SHORT_TERM_DESKS:
                close_plan[i] = "EOD_SQUAREOFF"

    for i, sq_status in close_plan.items():
        tr = trades_out[i]
        if tr.get("status") != "OPEN":
            continue
        ltp = float(tr.get("ltp", tr.get("entry", 0.0)) or 0.0)
        res = _finalize_exit(tr, sq_status, round(ltp, 2), now_ist_str)
        dk = still_open[i]
        detail = (f"Peak ₹{new_peak:,.0f} / Floor ₹{active_floor:,.0f}" if dk in ("intraday", "fno") and sq_status == "PROFIT_FLOOR_SQUAREOFF"
                  else f"Swing peak ₹{new_swing_peak:,.0f} / Floor ₹{swing_floor:,.0f}" if sq_status == "PROFIT_FLOOR_SQUAREOFF"
                  else f"{dk} day P&L limit ₹{cfg_for(dk).daily_loss_limit:,.0f}" if sq_status == "CIRCUIT_BREAKER_SQUAREOFF" else "15:15 IST")
        actions.append({
            "timestamp": now_ist_str, "symbol": str(tr.get("symbol", "")).upper(), "desk": str(tr.get("desk", "")), "action": "EXIT", "status": sq_status,
            "reason": f"{sq_status} @ ₹{ltp:.2f} (Net ₹{res['net_pnl']:+,.2f}; {detail})",
            "ltp": ltp, "sl": float(tr.get("sl", 0.0) or 0.0), "net_pnl": res["net_pnl"], "realized_r": res["realized_r"], "dry_run": dry_run,
        })

    return {
        "timestamp": now_ist_str, "evaluated_count": len(open_trades), "actions_count": len(actions),
        "actions": actions, "trades": trades_out,
        "combined_peak_pnl": new_peak, "locked_floor": active_floor,
        "swing_peak_pnl": new_swing_peak, "swing_floor": swing_floor,
        "breaker_tripped": breaker_tripped,
    }
