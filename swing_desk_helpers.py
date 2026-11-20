import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))
MAX_SWING_SLOTS = 15
MAX_SWING_PER_SECTOR = 3
MIN_SWING_RR = 1.60
SWING_ROUND_TRIP_COST_PCT = 0.0012


def validate_swing_entry_brackets(entry: float, initial_sl: float, target: float, min_rr: float = MIN_SWING_RR) -> tuple[bool, float, str]:
    if not (initial_sl < entry < target):
        return False, 0.0, f"Invalid swing brackets: initial_sl ({initial_sl}) < entry ({entry}) < target ({target}) required"
    risk = entry - initial_sl
    reward = target - entry
    rr = round(reward / max(risk, 1e-6), 2)
    if rr < min_rr:
        return False, rr, f"Swing R:R 1:{rr:.2f} below minimum 1:{min_rr:.2f}"
    return True, rr, f"Swing R:R 1:{rr:.2f} OK"


def ensure_swing_audit_columns(trades_ws):
    """Safely appends side, initial_sl, gross_pnl, charges_slippage in L1:O1 without altering existing A1:K1."""
    try:
        if trades_ws.acell("L1").value != "side":
            trades_ws.update("L1:O1", [["side", "initial_sl", "gross_pnl", "charges_slippage"]])
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
