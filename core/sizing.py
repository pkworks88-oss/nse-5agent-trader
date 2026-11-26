from typing import Any, Dict, List, Optional


def compute_position_sizing(
    desk_capital: float, deployed_capital: float, entry_price: float, stop_loss: float,
    target_price: float, conviction_score: float, max_pos_pct: float = 15.0,
    risk_per_trade_pct: float = 1.0, soft_cushion_pct: float = 10.0,
    baseline_slots: int = 15, current_open_slots: int = 0, sector: str = "GENERAL",
    sector_counts: Optional[Dict[str, int]] = None, max_per_sector: int = 3,
    allow_cushion_tap: bool = True, allow_overflow_slots: bool = False,
    lot_size: int = 1, atr: Optional[float] = None, beta: Optional[float] = None,
    max_overflow_slots: int = 0, margin_pct: float = 1.0,
) -> Dict[str, Any]:
    sector_counts = sector_counts or {}
    if desk_capital <= 0:
        return {"approved": False, "qty": 0, "raw_risk_qty": 0, "capped_qty": 0, "notional": 0.0, "effective_stop_dist": 0.0, "allocation_tag": "DESK_UNFUNDED", "reason": "Desk capital is ₹0."}

    if sector_counts.get(sector, 0) >= max_per_sector:
        return {"approved": False, "qty": 0, "raw_risk_qty": 0, "capped_qty": 0, "notional": 0.0, "effective_stop_dist": 0.0, "allocation_tag": "BLOCKED_SECTOR_CAP", "reason": f"Sector {sector} already at max {max_per_sector} positions."}

    hard_limit = baseline_slots + (max(0, int(max_overflow_slots)) if allow_overflow_slots else 0)
    if current_open_slots >= hard_limit or (current_open_slots >= baseline_slots and not (allow_overflow_slots and conviction_score >= 90)):
        return {"approved": False, "qty": 0, "raw_risk_qty": 0, "capped_qty": 0, "notional": 0.0, "effective_stop_dist": 0.0, "allocation_tag": "BLOCKED_SLOT_CAP", "reason": f"Slots full ({current_open_slots}/{baseline_slots} baseline, hard limit {hard_limit}; overflow needs conviction >= 90)."}

    min_stop_floor = max(0.05, entry_price * 0.005)
    raw_stop_dist = abs(entry_price - stop_loss)
    stop_dist = max(min_stop_floor, raw_stop_dist, 0.75 * float(atr)) if (atr is not None and atr > 0) else max(min_stop_floor, raw_stop_dist)

    effective_risk_pct = float(risk_per_trade_pct)
    beta_val = float(beta) if beta is not None else 1.0
    if beta is not None and beta_val > 1.5:
        effective_risk_pct = max(0.25, round(effective_risk_pct * 0.75, 4))

    risk_budget = desk_capital * (effective_risk_pct / 100.0)
    raw_qty = int(risk_budget // max(stop_dist, 0.05))
    margin = min(1.0, max(0.01, float(margin_pct)))     # cash actually blocked per rupee of notional
    max_pos_notional = desk_capital * (max_pos_pct / 100.0)
    cap_qty = int(max_pos_notional // max(entry_price * margin, 0.01))

    soft_buffer = desk_capital * (soft_cushion_pct / 100.0)
    raw_available = max(0.0, desk_capital - deployed_capital)
    core_available = max(0.0, raw_available - soft_buffer)

    qty = min(raw_qty, cap_qty)
    if lot_size > 1:
        qty = (qty // lot_size) * lot_size

    notional = qty * entry_price
    cash_req = notional * margin
    tag = "STANDARD_ALLOC"
    notes: List[str] = []

    if beta is not None and beta_val > 1.5:
        notes.append(f"High-beta ({beta_val:.2f}) adjusted risk to {effective_risk_pct:.2f}%.")
    if raw_qty > cap_qty:
        tag = "CAPPED_BY_MAX_POS"
        notes.append(f"Anti-cash-hog cap reduced qty from {raw_qty} to {cap_qty} ({max_pos_pct:.0f}% max pos).")

    if cash_req > core_available:
        if allow_cushion_tap and conviction_score >= 85 and cash_req <= raw_available:
            tag = "BUFFER_10PCT_USED"
            notes.append(f"High conviction ({conviction_score:.0f}/100) tapped soft {soft_cushion_pct:.0f}% cushion.")
        else:
            affordable_qty = int(core_available // max(entry_price * margin, 0.01))
            if lot_size > 1:
                affordable_qty = (affordable_qty // lot_size) * lot_size
            qty = max(0, affordable_qty)
            notional = qty * entry_price
            cash_req = notional * margin

    if current_open_slots >= baseline_slots and qty > 0:
        tag = "SLOT_OVERFLOW"
        notes.append(f"Slot #{current_open_slots + 1} permitted via high-conviction overflow.")

    if qty <= 0:
        return {
            "approved": False, "qty": 0, "raw_risk_qty": raw_qty, "capped_qty": cap_qty,
            "notional": 0.0, "effective_stop_dist": round(stop_dist, 4),
            "allocation_tag": "BLOCKED_RISK_OR_CASH_LIMIT",
            "reason": f"Qty=0 after lot/risk/cash caps (min lot cost ₹{lot_size * entry_price * margin:,.0f}, min lot risk ₹{lot_size * stop_dist:,.0f} vs risk budget ₹{risk_budget:,.0f}).",
        }

    return {
        "approved": True, "qty": qty, "raw_risk_qty": raw_qty, "capped_qty": cap_qty,
        "notional": round(notional, 2), "cash_required": round(cash_req, 2), "effective_stop_dist": round(stop_dist, 4),
        "allocation_tag": tag,
        "reason": " ".join(notes) if notes else "Sized within standard risk & notional guardrails.",
    }
