from typing import Dict


def estimate_roundtrip_charges(entry_price: float, exit_price: float, qty: int, desk: str = "Swing") -> float:
    if qty <= 0 or entry_price <= 0:
        return 0.0
    turnover = (entry_price + exit_price) * qty
    d = desk.strip().lower().replace("&", "").replace(" ", "")
    if d == "fo":
        d = "fno"
    if d == "swing":
        brokerage = min(40.0, turnover * 0.0003)
        stt = turnover * 0.001
        txn_charges = turnover * 0.0000345
        gst = 0.18 * (brokerage + txn_charges)
        slippage = turnover * 0.0004
    elif d == "intraday":
        brokerage = min(40.0, turnover * 0.0003)
        stt = exit_price * qty * 0.00025
        txn_charges = turnover * 0.0000345
        gst = 0.18 * (brokerage + txn_charges)
        slippage = turnover * 0.0003
    elif d == "fno":
        brokerage = 40.0
        stt = exit_price * qty * 0.001
        txn_charges = turnover * 0.00053
        gst = 0.18 * (brokerage + txn_charges)
        slippage = turnover * 0.0008
    else:
        brokerage = 40.0
        stt = exit_price * qty * 0.0001
        txn_charges = turnover * 0.000026
        gst = 0.18 * (brokerage + txn_charges)
        slippage = turnover * 0.0005
    return round(brokerage + stt + txn_charges + gst + slippage, 2)


def compute_net_rr(entry: float, sl: float, target: float, qty: int, desk: str = "Swing") -> float:
    risk_per_unit = abs(entry - sl)
    reward_per_unit = abs(target - entry)
    if risk_per_unit <= 0 or qty <= 0:
        return 0.0
    charges = estimate_roundtrip_charges(entry, target, qty, desk=desk)
    net_reward = max(0.0, (reward_per_unit * qty) - charges)
    total_risk = (risk_per_unit * qty) + (charges * 0.5)
    return round(net_reward / max(total_risk, 1.0), 2)


def summarize_pnl(entry: float, current_or_exit: float, qty: int, side: str = "LONG", desk: str = "Swing") -> Dict[str, float]:
    direction = -1.0 if str(side).upper() == "SHORT" else 1.0
    gross = round((current_or_exit - entry) * qty * direction, 2)
    charges = estimate_roundtrip_charges(entry, current_or_exit, qty, desk=desk)
    net = round(gross - charges, 2)
    return {"gross_pnl": gross, "charges": charges, "net_pnl": net}
