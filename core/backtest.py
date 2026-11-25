from typing import Any, Dict, List
from core.charges import compute_net_rr, estimate_roundtrip_charges
from core.config import EARNINGS_BLACKOUT_HOURS, MAX_POSITIONS_PER_SECTOR
from core.sizing import compute_position_sizing


def run_parity_backtest(
    candidates: List[Dict[str, Any]], desk_capital: float = 1000000.0,
    max_pos_pct: float = 15.0, risk_per_trade_pct: float = 1.0,
    soft_cushion_pct: float = 10.0, baseline_slots: int = 15,
    allow_cushion_tap: bool = True, allow_overflow_slots: bool = False,
) -> Dict[str, Any]:
    deployed = 0.0
    open_slots = 0
    sector_counts: Dict[str, int] = {}
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for c in candidates:
        sym = c["symbol"]
        sector = c.get("sector", "GENERAL")
        entry = float(c["entry"])
        sl = float(c["sl"])
        target = float(c["target"])
        pdh = float(c.get("pdh", entry * 1.01))
        pdl = float(c.get("pdl", entry * 0.99))
        earnings_h = int(c.get("earnings_hours", 240))
        conv = float(c.get("conviction", 82.0))
        desk = c.get("desk", "Swing")
        lot_sz = int(c.get("lot_size", 1) or 1)

        if earnings_h < EARNINGS_BLACKOUT_HOURS:
            rejected.append({"symbol": sym, "reason": f"EARNINGS_BLACKOUT_{earnings_h}H (<48h)"})
            continue
        if entry < pdl:
            rejected.append({"symbol": sym, "reason": f"BELOW_PDL (Entry {entry} < PDL {pdl})"})
            continue

        sizing = compute_position_sizing(
            desk_capital=desk_capital, deployed_capital=deployed,
            entry_price=entry, stop_loss=sl, target_price=target, conviction_score=conv,
            max_pos_pct=max_pos_pct, risk_per_trade_pct=risk_per_trade_pct,
            soft_cushion_pct=soft_cushion_pct, baseline_slots=baseline_slots,
            current_open_slots=open_slots, sector=sector, sector_counts=sector_counts,
            max_per_sector=MAX_POSITIONS_PER_SECTOR, allow_cushion_tap=allow_cushion_tap,
            allow_overflow_slots=allow_overflow_slots, lot_size=lot_sz,
        )
        if not sizing["approved"]:
            rejected.append({"symbol": sym, "reason": sizing["reason"]})
            continue

        qty = sizing["qty"]
        net_rr = compute_net_rr(entry, sl, target, qty, desk=desk)
        if net_rr < 1.60:
            rejected.append({"symbol": sym, "reason": f"NET_RR_BELOW_1_60 (1:{net_rr:.2f})"})
            continue

        charges = estimate_roundtrip_charges(entry, target, qty, desk=desk)
        deployed += sizing["notional"]
        open_slots += 1
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        accepted.append({
            "symbol": sym, "desk": desk, "sector": sector, "qty": qty,
            "notional": sizing["notional"], "allocation_tag": sizing["allocation_tag"],
            "net_rr": net_rr, "est_charges": charges, "pdh": pdh, "pdl": pdl,
        })

    return {"schema": "v4.8", "accepted_count": len(accepted), "rejected_count": len(rejected), "deployed_capital": round(deployed, 2), "accepted": accepted, "rejected": rejected}


def run_historical_backtest(years: int = 3, overrides: Dict[str, Any] | None = None, max_entries_per_day: int = 3,
                            data: Dict[str, Any] | None = None, meta: Dict[str, Any] | None = None,
                            index_df: Any = None, vix_df: Any = None) -> Dict[str, Any]:
    """REAL swing backtest (daily bars, live strategy code, next-open fills). Pass data=... to run offline."""
    from core.backtest_engine import run_daily_backtest, run_swing_backtest_from_yahoo
    if data is not None:
        return run_daily_backtest(data, meta or {}, index_df, vix_df, desk="swing", overrides=overrides, max_entries_per_day=max_entries_per_day)
    return run_swing_backtest_from_yahoo(years=years, overrides=overrides, max_entries_per_day=max_entries_per_day)


def run_intraday_mis_backtest(overrides: Dict[str, Any] | None = None, intraday: Dict[str, Any] | None = None,
                              daily: Dict[str, Any] | None = None, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """REAL intraday backtest (5-minute bars, ~59 days of Yahoo history, next-bar fills, 15:15 square-off)."""
    from core.backtest_engine import run_intraday_backtest, run_intraday_backtest_from_yahoo
    if intraday is not None and daily is not None:
        return run_intraday_backtest(intraday, daily, meta or {}, desk="intraday", overrides=overrides)
    return run_intraday_backtest_from_yahoo(overrides=overrides)
