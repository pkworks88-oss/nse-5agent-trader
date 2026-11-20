import numpy as np
import pandas as pd
import yfinance as yf
from skills.market_skills import compute_adx_series


def run_intraday_mis_backtest(
    symbol: str = "RELIANCE.NS",
    lookback_days: int = 10,
    init_capital: float = 100_000.0,
    leverage: float = 5.0,
    risk_per_trade: float = 550.0,
    daily_loss_limit: float = -1_500.0,
    slippage_pct: float = 0.0005,
    min_adx: float = 20.0,
) -> dict:
    """Offline 5m-bar backtester for Intraday MIS with 20% target-progress trailing SL & daily loss guard."""
    period_str = f"{max(5, min(59, lookback_days))}d"
    try:
        df = yf.Ticker(symbol).history(period=period_str, interval="5m")
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty or len(df) < 30:
        return {
            "symbol": symbol,
            "trades_count": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "max_drawdown": 0.0,
            "equity_curve": [init_capital],
            "trade_log": [],
        }

    df = df.dropna().copy()
    df["trade_date"] = df.index.strftime("%Y-%m-%d")
    df["ema9"] = df["Close"].ewm(span=9).mean()
    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["atr14"] = (df["High"] - df["Low"]).rolling(14).mean()
    df["adx14"] = compute_adx_series(df["High"], df["Low"], df["Close"], period=14)

    cash = float(init_capital)
    peak_eq = float(init_capital)
    max_dd = 0.0
    equity_curve = [round(cash, 2)]
    trade_log = []

    for d_str, day_df in df.groupby("trade_date", sort=True):
        if len(day_df) < 12:
            continue
        day_df = day_df.copy()
        typ = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3.0
        day_df["vwap"] = (typ * day_df["Volume"]).cumsum() / day_df["Volume"].cumsum().replace(0, np.nan)

        day_pnl = 0.0
        pos = None

        for i, (ts, bar) in enumerate(day_df.iterrows()):
            px_c = float(bar["Close"])
            px_h = float(bar["High"])
            px_l = float(bar["Low"])
            atr_v = float(bar["atr14"]) if pd.notna(bar["atr14"]) and bar["atr14"] > 0 else px_c * 0.005
            adx_v = float(bar["adx14"]) if pd.notna(bar["adx14"]) else 0.0
            vwap_v = float(bar["vwap"]) if pd.notna(bar["vwap"]) else px_c

            # Exit check if in position
            if pos is not None:
                entry = pos["entry"]
                sl = pos["sl"]
                target = pos["target"]
                qty = pos["qty"]
                pos["peak_high"] = max(pos["peak_high"], px_h)
                dist = max(target - entry, 0.25)
                prog = (pos["peak_high"] - entry) / dist

                # 20% target-progress trailing ladder
                if prog >= 0.80:
                    sl = max(sl, round(entry + 0.65 * dist, 2))
                elif prog >= 0.60:
                    sl = max(sl, round(entry + 0.40 * dist, 2))
                elif prog >= 0.40:
                    sl = max(sl, round(entry + 0.20 * dist, 2))
                elif prog >= 0.20:
                    sl = max(sl, round(entry * 1.001, 2))
                pos["sl"] = sl

                exit_px = None
                reason = None
                if px_l <= sl:
                    exit_px = round(sl * (1.0 - slippage_pct), 2)
                    reason = "TRAILING_SL_HIT" if sl >= entry else "SL_HIT"
                elif px_h >= target:
                    exit_px = round(target * (1.0 - slippage_pct), 2)
                    reason = "TARGET_HIT"
                elif i >= len(day_df) - 3:
                    exit_px = round(px_c * (1.0 - slippage_pct), 2)
                    reason = "EOD_SQUAREOFF"

                if exit_px is not None:
                    gross = (exit_px - entry) * qty
                    costs = round((entry + exit_px) * qty * (0.0006 + slippage_pct), 2)
                    net = round(gross - costs, 2)
                    cash = round(cash + ((entry * qty) / leverage) + net, 2)
                    day_pnl = round(day_pnl + net, 2)
                    peak_eq = max(peak_eq, cash)
                    max_dd = max(max_dd, round(peak_eq - cash, 2))
                    equity_curve.append(cash)
                    trade_log.append({
                        "date": d_str,
                        "exit_time": str(ts),
                        "qty": qty,
                        "entry": entry,
                        "exit": exit_px,
                        "sl_final": sl,
                        "target": target,
                        "net_pnl": net,
                        "reason": reason,
                    })
                    pos = None

            # Entry check (after first 3 ORB bars and before last 10 bars)
            if pos is None and 3 <= i < len(day_df) - 10 and day_pnl > daily_loss_limit:
                ema9_v = float(bar["ema9"]) if pd.notna(bar["ema9"]) else px_c
                ema20_v = float(bar["ema20"]) if pd.notna(bar["ema20"]) else px_c
                if px_c > vwap_v and ema9_v > ema20_v and adx_v >= min_adx:
                    sl_init = round(px_c - atr_v, 2)
                    tp_init = round(px_c + 2.0 * atr_v, 2)
                    risk_sh = max(px_c - sl_init, px_c * 0.003)
                    qty_r = int(risk_per_trade // risk_sh)
                    max_notional = cash * leverage * 0.33
                    qty_c = int(max_notional // px_c)
                    qty = max(0, min(qty_r, qty_c))
                    margin = round((qty * px_c) / leverage, 2)
                    if qty > 0 and margin <= cash:
                        fill_entry = round(px_c * (1.0 + slippage_pct), 2)
                        cash = round(cash - margin, 2)
                        pos = {
                            "entry": fill_entry,
                            "sl": sl_init,
                            "target": tp_init,
                            "qty": qty,
                            "peak_high": fill_entry,
                        }

    wins = sum(1 for t in trade_log if t["net_pnl"] > 0)
    total_t = len(trade_log)
    win_rate = round((wins / total_t) * 100.0, 1) if total_t > 0 else 0.0
    net_total = round(cash - init_capital, 2)
    return {
        "symbol": symbol,
        "trades_count": total_t,
        "win_rate_pct": win_rate,
        "net_pnl": net_total,
        "max_drawdown": max_dd,
        "equity_curve": equity_curve,
        "trade_log": trade_log,
    }
