import json
from typing import Any, Dict, List
from core.config import get_desk_config


def build_8_stage_audit(
    symbol: str, desk: str, tier: str, entry: float, sl: float, target: float,
    rsi: float, rvol: float, pdh: float, pdl: float, net_rr: float,
    sector: str, sector_count: int, earnings_hours: int,
    sizing_result: Dict[str, Any], sim_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    cfg = get_desk_config(desk)
    blackout = earnings_hours < 48
    rsi_ok = rsi <= cfg.rsi_cap
    pdl_ok = entry >= pdl
    rr_ok = net_rr >= cfg.min_rr
    sector_ok = sector_count < 3
    size_ok = bool(sizing_result.get("approved", False))
    alloc_tag = sizing_result.get("allocation_tag", "STANDARD_ALLOC")
    approved = size_ok and not blackout and rsi_ok and pdl_ok and rr_ok and sector_ok

    block_reasons = []
    if blackout:
        block_reasons.append(f"Earnings blackout ({earnings_hours}h < 48h)")
    if not rsi_ok:
        block_reasons.append(f"RSI {rsi:.1f} > cap {cfg.rsi_cap:.1f}")
    if not pdl_ok:
        block_reasons.append(f"Entry ₹{entry:.2f} < PDL ₹{pdl:.2f}")
    if not rr_ok:
        block_reasons.append(f"Net R:R 1:{net_rr:.2f} < 1:{cfg.min_rr:.2f}")
    if not sector_ok:
        block_reasons.append(f"Sector {sector} {sector_count}/3 full")
    if not size_ok:
        block_reasons.append(sizing_result.get("reason", "Sizing blocked"))

    return [
        {"stage_num": 1, "stage": "Scanner & Liquidity", "status": "PASS" if rvol >= 1.10 else "WATCH", "summary": f"RVOL {rvol:.2f}x | Tier {tier} | Desk {desk}", "why": f"{symbol} scanned with RVOL {rvol:.2f}x on {desk} desk.", "metrics_json": json.dumps({"symbol": symbol, "desk": desk, "tier": tier, "rvol": rvol})},
        {"stage_num": 2, "stage": "News & <48h Earnings Guard", "status": "BLOCKED" if blackout else "PASS", "summary": f"Next Earnings: {earnings_hours}h | Blackout: {blackout}", "why": f"Blocked: earnings within {earnings_hours}h (<48h blackout)." if blackout else f"Clear of 48h earnings blackout ({earnings_hours}h buffer).", "metrics_json": json.dumps({"earnings_hours": earnings_hours, "blackout_48h": blackout})},
        {"stage_num": 3, "stage": "Entry, RSI Cap & PDH/PDL", "status": "PASS" if (rsi_ok and pdl_ok) else "BLOCKED", "summary": f"RSI {rsi:.1f}/{cfg.rsi_cap:.0f} | PDH ₹{pdh:.2f} / PDL ₹{pdl:.2f}", "why": f"Entry ₹{entry:.2f} >= PDL ₹{pdl:.2f} and RSI {rsi:.1f} <= {cfg.rsi_cap:.0f}." if (rsi_ok and pdl_ok) else f"Blocked: RSI {rsi:.1f} vs {cfg.rsi_cap:.0f} cap or Entry ₹{entry:.2f} < PDL ₹{pdl:.2f}.", "metrics_json": json.dumps({"entry": entry, "rsi": rsi, "rsi_cap": cfg.rsi_cap, "pdh": pdh, "pdl": pdl})},
        {"stage_num": 4, "stage": "Net R:R & Sector Concentration", "status": "PASS" if (rr_ok and sector_ok) else "BLOCKED", "summary": f"Net R:R 1:{net_rr:.2f} | {sector} {sector_count}/3", "why": f"Net-of-charges R:R is 1:{net_rr:.2f} (>=1:{cfg.min_rr:.2f}) with {sector} sector at {sector_count}/3 cap.", "metrics_json": json.dumps({"net_rr": net_rr, "sector": sector, "sector_count": f"{sector_count}/3"})},
        {"stage_num": 5, "stage": "Anti-Cash-Hog Sizing & Cushion", "status": "WATCH" if alloc_tag in ("CAPPED_BY_MAX_POS", "BUFFER_10PCT_USED", "SLOT_OVERFLOW") else ("PASS" if size_ok else "BLOCKED"), "summary": f"{alloc_tag} | Qty {sizing_result.get('qty', 0)} (Raw {sizing_result.get('raw_risk_qty', 0)})", "why": sizing_result.get("reason", "Position sized within user-configured desk limits."), "metrics_json": json.dumps(sizing_result)},
        {"stage_num": 6, "stage": "Exit & Trailing Ladder", "status": "ARMED" if approved else "WATCH", "summary": f"{cfg.trail_be_pct:.0f}% BE + {cfg.trail_step_pct:.0f}% Step Trail | 15:15 IST EOD", "why": "Per-desk trailing profit lock activates once SL steps above entry; Intraday/F&O/Commodity square off at 15:15 IST.", "metrics_json": json.dumps({"be_pct": cfg.trail_be_pct, "step_pct": cfg.trail_step_pct, "eod_ist": "15:15"})},
        {"stage_num": 7, "stage": "AG4 Chief Analyst Verdict", "status": "APPROVED" if approved else "BLOCKED", "summary": f"{'APPROVED' if approved else 'VETOED'} | Tag: {alloc_tag}", "why": f"AG4_CHIEF_ANALYST approved {symbol} ({tier}) for AG5_EXECUTION_BROKER paper fill." if approved else f"AG4_CHIEF_ANALYST vetoed {symbol}: {'; '.join(block_reasons)}.", "metrics_json": json.dumps({"verdict": "APPROVED" if approved else "VETOED", "reasons": block_reasons, "schema": "v4.8"})},
        {"stage_num": 8, "stage": "Advisory Scenario Simulator", "status": "PASS" if sim_result.get("target_first_pct", 0) >= 45 else "WATCH", "summary": f"Target-First {sim_result.get('target_first_pct', 0)}% | Exp Net ₹{sim_result.get('expected_net_pnl', 0):,.0f}", "why": "Zero-drift historical/bootstrap excursion advisory check.", "metrics_json": json.dumps({k: v for k, v in sim_result.items() if k != "fan_paths"})},
    ]
