import json
from datetime import datetime, timezone, timedelta
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
INIT_SWING_CAPITAL = 1_000_000.0
MAX_SWING_SLOTS = 15
MAX_SWING_PER_SECTOR = 3
MIN_SWING_RR = 1.60
MAX_SWING_RSI = 72.0
SWING_ROUND_TRIP_COST_PCT = 0.0012


def validate_swing_entry_brackets(
    entry: float,
    initial_sl: float,
    target: float,
    min_rr: float = MIN_SWING_RR,
    rsi_14: float | None = None,
    max_rsi: float = MAX_SWING_RSI,
) -> tuple[bool, float, str]:
    if not (initial_sl < entry < target):
        return False, 0.0, f"Invalid swing brackets: initial_sl ({initial_sl}) < entry ({entry}) < target ({target}) required"
    if rsi_14 is not None and float(rsi_14) > max_rsi:
        return False, 0.0, f"Swing RSI(14) {rsi_14:.1f} exceeds cap {max_rsi:.1f}"
    risk = entry - initial_sl
    reward = target - entry
    rr = round(reward / max(risk, 1e-6), 2)
    if rr < min_rr:
        return False, rr, f"Swing R:R 1:{rr:.2f} below minimum 1:{min_rr:.2f}"
    return True, rr, f"Swing R:R 1:{rr:.2f} OK"


def read_swing_account_state(sh, default_cash: float = INIT_SWING_CAPITAL) -> dict:
    """Reads Swing account worksheet dynamically by header name so shifted columns never corrupt cash/peak."""
    state = {"cash": float(default_cash), "peak_floor_ref": 0.0}
    try:
        acct_ws = sh.worksheet("account")
        rows = acct_ws.get_all_records()
        if rows:
            r0 = rows[0]
            norm_map = {str(k).strip().lower(): v for k, v in r0.items()}
            for key in ("cash", "free_cash", "swing_cash", "cash_balance", "available_cash"):
                if key in norm_map and norm_map[key] not in ("", None):
                    state["cash"] = float(norm_map[key])
                    break
            for pkey in ("peak_floor_ref", "peak_pnl", "locked_floor", "peak_equity", "peak"):
                if pkey in norm_map and norm_map[pkey] not in ("", None):
                    state["peak_floor_ref"] = float(norm_map[pkey])
                    break
            return state
        # Fallback if header row is non-standard
        vals = acct_ws.get("A2:E2")
        if vals and vals[0] and len(vals[0]) > 1 and vals[0][1] not in ("", None):
            state["cash"] = float(vals[0][1])
    except Exception:
        pass
    return state


def ensure_swing_audit_columns(trades_ws, backfill_missing: bool = False):
    """Safely appends side, initial_sl, gross_pnl, charges_slippage in L1:O1 without altering A1:K1."""
    try:
        if trades_ws.acell("L1").value != "side":
            trades_ws.update("L1:O1", [["side", "initial_sl", "gross_pnl", "charges_slippage"]])
        if backfill_missing:
            records = trades_ws.get_all_records()
            updates = []
            for idx, r in enumerate(records, start=2):
                side_v = str(r.get("side", "")).strip()
                init_sl_v = r.get("initial_sl", "")
                if not side_v or init_sl_v in ("", None):
                    entry = float(r.get("entry", 0) or 0)
                    sl = float(r.get("sl", 0) or 0)
                    # If SL was already trailed above entry, estimate conservative initial SL at entry * 0.97
                    inferred_sl = sl if (0 < sl < entry) else round(entry * 0.97, 2)
                    pnl_v = float(r.get("pnl", 0) or 0)
                    qty_v = int(float(r.get("qty", 0) or 0))
                    is_closed = str(r.get("status", "")).upper() != "OPEN"
                    charges = round(entry * qty_v * 2.0 * SWING_ROUND_TRIP_COST_PCT, 2) if is_closed else 0.0
                    gross = round(pnl_v + charges, 2) if is_closed else 0.0
                    updates.append({
                        "range": f"L{idx}:O{idx}",
                        "values": [["BUY", inferred_sl, gross, charges]],
                    })
            if updates:
                trades_ws.batch_update(updates)
    except Exception:
        pass


def compute_swing_open_downside_risk(open_df: pd.DataFrame) -> float:
    if open_df is None or open_df.empty:
        return 0.0
    total_risk = 0.0
    for _, r in open_df.iterrows():
        entry = float(r.get("entry", 0) or 0)
        sl = float(r.get("sl", 0) or 0)
        qty = int(float(r.get("qty", 0) or 0))
        if sl < entry:
            total_risk += (entry - sl) * qty
    return round(total_risk, 2)


def record_swing_lesson_row(msg_ws, cycle_id: str, symbol: str, tier: str, exit_tag: str, net_pnl: float, reason: str):
    outcome = "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "FLAT")
    if outcome == "WIN":
        takeaway = f"Swing trend ({tier} / {exit_tag}) captured ₹{net_pnl:+,.2f} on {symbol}; multi-day trailing stop preserved upside."
    else:
        takeaway = f"Swing {symbol} ({tier}) exited via {exit_tag} ({net_pnl:+,.2f}); review weekly 20-EMA & sector concentration."
    payload = {
        "book": "SWING",
        "symbol": symbol,
        "tier": tier,
        "exit_tag": exit_tag,
        "net_pnl": round(float(net_pnl), 2),
        "outcome": outcome,
        "source_reason": str(reason)[:100],
    }
    try:
        msg_ws.append_row([
            cycle_id,
            datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            "AG4_CHIEF_ANALYST",
            "SWING_MEMORY",
            "AGENT_LESSON",
            f"🧠 [{outcome} | SWING] {symbol}: {takeaway}",
            json.dumps(payload),
        ])
    except Exception:
        pass


def render_circular_trust_badge(agent_label: str, role_sub: str, score: int, detail_line: str = "", baseline: int = 50) -> str:
    """Renders an SVG circular radial trust ring with dynamic color glow and +/- delta badge from baseline."""
    sc = max(0, min(100, int(score)))
    delta = sc - int(baseline)
    radius = 27
    circumference = 2 * 3.14159265 * radius
    dash_offset = circumference * (1.0 - (sc / 100.0))
    if sc >= 65:
        ring_col = "#10b981"
        glow = "0 0 14px rgba(16,185,129,0.32)"
    elif sc >= 50:
        ring_col = "#38bdf8"
        glow = "0 0 14px rgba(56,189,248,0.30)"
    else:
        ring_col = "#f59e0b"
        glow = "0 0 14px rgba(245,158,11,0.32)"

    if delta > 0:
        d_badge = f"<span style='background:rgba(16,185,129,0.18);color:#34d399;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:700;margin-left:6px;'>+{delta}</span>"
    elif delta < 0:
        d_badge = f"<span style='background:rgba(239,68,68,0.18);color:#f87171;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:700;margin-left:6px;'>{delta}</span>"
    else:
        d_badge = "<span style='background:rgba(148,163,184,0.16);color:#94a3b8;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:700;margin-left:6px;'>0</span>"

    sub_html = f"<div style='font-size:11px;color:#94a3b8;margin-top:5px;'>{detail_line}</div>" if detail_line else ""
    return (
        f"<div class='panel-box' style='display:flex;align-items:center;gap:12px;box-shadow:{glow};'>"
        f"<svg width='66' height='66' viewBox='0 0 66 66' style='flex-shrink:0;'>"
        f"<circle cx='33' cy='33' r='{radius}' fill='none' stroke='#1e293b' stroke-width='6'/>"
        f"<circle cx='33' cy='33' r='{radius}' fill='none' stroke='{ring_col}' stroke-width='6' "
        f"stroke-dasharray='{circumference:.1f}' stroke-dashoffset='{dash_offset:.1f}' "
        f"stroke-linecap='round' transform='rotate(-90 33 33)'/>"
        f"<text x='33' y='37' text-anchor='middle' fill='#f8fafc' font-size='14' font-weight='800'>{sc}</text>"
        f"</svg>"
        f"<div>"
        f"<div style='font-size:13px;font-weight:800;color:#f8fafc;'>{agent_label}{d_badge}</div>"
        f"<div style='font-size:11.5px;color:#38bdf8;font-weight:600;'>{role_sub} • {sc}/100</div>"
        f"{sub_html}"
        f"</div></div>"
    )
