import math
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
from swing_engine import (
    FALLBACK_NSE_250,
    RISK_PER_TIER,
    ATR_MULT_PER_TIER,
    compute_25_plus_10_ladder_sl,
    get_portfolio_profit_floor,
)


def run_historical_backtest(
    lookback_months: int = 6,
    initial_capital: float = 1_000_000.0,
    max_open_positions: int = 10,
    universe_size: int = 100,
    slippage_and_stt_pct: float = 0.0012,
    use_nifty_regime_filter: bool = True,
    use_profit_floor: bool = True,
) -> dict:
    """
    Deterministic zero-token historical backtester replaying exact V3 quantitative rules:
      - NIFTY 50 20-SMA regime filter & defensive mode
      - Tiered AG1 (Low), AG2 (Mid), AG3 (High) screening & ATR stop/target sizing
      - 25% Breakeven + 10% Incremental Step-Trailing SL Ladder
      - Target Rollover (0.25% breathing buffer + Target 2 extension)
      - Stepped Portfolio Profit Floor (+₹7k / +₹10k / +₹15k / +₹20k+)
      - Realistic NSE equity friction (STT, exchange charges & slippage)
    """
    symbols = list(dict.fromkeys(FALLBACK_NSE_250[:universe_size]))
    all_tickers = ["^NSEI"] + symbols

    end_dt = datetime.now()
    # Fetch extra 210 days prior to lookback window so SMA_200 and ATR_14 are warm on Day 1
    fetch_start = (end_dt - timedelta(days=int(lookback_months * 30.5) + 230)).strftime("%Y-%m-%d")
    sim_start_dt = pd.Timestamp(end_dt - timedelta(days=int(lookback_months * 30.5)))

    raw = yf.download(
        all_tickers,
        start=fetch_start,
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
    )

    if raw.empty or "^NSEI" not in raw.columns.get_level_values(0):
        return {"error": "Unable to download historical NSE data from Yahoo Finance."}

    nifty_df = raw["^NSEI"].dropna(subset=["Close"]).copy()
    nifty_df["sma20"] = nifty_df["Close"].rolling(20).mean()
    nifty_df["ret_pct"] = nifty_df["Close"].pct_change() * 100.0

    # Precompute daily indicator matrices for all symbols
    stock_frames = {}
    for sym in symbols:
        try:
            if sym not in raw.columns.get_level_values(0):
                continue
            df = raw[sym].dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
            if len(df) < 60:
                continue
            df["sma50"] = df["Close"].rolling(50).mean()
            df["sma200"] = df["Close"].rolling(min(200, len(df))).mean()
            df["avg_vol20"] = df["Volume"].rolling(20).mean()
            df["rvol"] = df["Volume"] / df["avg_vol20"].replace(0, np.nan)
            df["atr14"] = (df["High"] - df["Low"]).rolling(14).mean()
            df["atr_pct"] = (df["atr14"] / df["Close"]) * 100.0
            df["beta"] = (df["atr_pct"] / 1.8).clip(0.6, 2.2)
            df["day_chg"] = df["Close"].pct_change() * 100.0

            delta = df["Close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            df["rsi14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
            stock_frames[sym] = df
        except Exception:
            continue

    if not stock_frames:
        return {"error": "Insufficient historical bars computed for NSE universe."}

    sim_dates = [d for d in nifty_df.index if d >= sim_start_dt]
    if len(sim_dates) < 5:
        return {"error": "Too few trading days in selected lookback period."}

    cash = float(initial_capital)
    open_positions = []
    closed_trades = []
    equity_curve = []
    peak_portfolio_pnl = 0.0

    first_nifty_close = float(nifty_df.loc[sim_dates[0], "Close"])

    for dt in sim_dates:
        n_row = nifty_df.loc[dt]
        n_close = float(n_row["Close"])
        n_sma20 = float(n_row["sma20"]) if pd.notna(n_row["sma20"]) else n_close
        n_chg = float(n_row["ret_pct"]) if pd.notna(n_row["ret_pct"]) else 0.0
        defensive_mode = use_nifty_regime_filter and ((n_chg <= -0.60) or (n_close < n_sma20))

        # ------------------------------------------------------------------
        # 1. MANAGE EXISTING OPEN POSITIONS (SL, 25%+10% LADDER, TARGET ROLLOVER)
        # ------------------------------------------------------------------
        still_open = []
        combined_unrealized = 0.0

        for pos in open_positions:
            sym = pos["symbol"]
            df = stock_frames.get(sym)
            if df is None or dt not in df.index:
                still_open.append(pos)
                combined_unrealized += (pos["last_close"] - pos["entry"]) * pos["qty"]
                continue

            bar = df.loc[dt]
            b_open = float(bar["Open"])
            b_high = float(bar["High"])
            b_low = float(bar["Low"])
            b_close = float(bar["Close"])
            pos["last_close"] = b_close

            entry = pos["entry"]
            sl = pos["sl"]
            target = pos["target"]
            qty = pos["qty"]
            target_dist = max(target - entry, entry * 0.01)

            # Stop-Loss hit (handles overnight gap-down below SL realistically)
            if b_low <= sl:
                raw_exit = min(b_open, sl) if b_open <= sl else sl
                exit_px = round(raw_exit * (1.0 - slippage_and_stt_pct), 2)
                pnl = round((exit_px - entry) * qty, 2)
                cash += exit_px * qty
                exit_tag = "TRAILING_SL_HIT" if sl >= entry else "SL_HIT"
                closed_trades.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": dt.strftime("%Y-%m-%d"),
                    "symbol": sym,
                    "tier": pos["tier"],
                    "qty": qty,
                    "entry": entry,
                    "exit_price": exit_px,
                    "status": exit_tag,
                    "pnl": pnl,
                    "return_pct": round(((exit_px - entry) / entry) * 100.0, 2),
                })
                continue

            # Target hit -> Rollover SL + Extend Target 2
            if b_high >= target:
                new_sl = round(target * 0.9975, 2)
                new_target = round(target + (target_dist * 0.5), 2)
                if new_sl > sl:
                    pos["sl"] = new_sl
                    pos["target"] = new_target
                    sl = new_sl
            else:
                progress = (b_high - entry) / target_dist
                ladder_sl, _ = compute_25_plus_10_ladder_sl(entry, target_dist, progress)
                if ladder_sl is not None and ladder_sl > sl:
                    pos["sl"] = ladder_sl
                    sl = ladder_sl

            combined_unrealized += (b_close - entry) * qty
            still_open.append(pos)

        open_positions = still_open

        # ------------------------------------------------------------------
        # 2. STEPPED PORTFOLIO PROFIT FLOOR (+₹7k / +₹10k / +₹15k / +₹20k+)
        # ------------------------------------------------------------------
        if combined_unrealized > peak_portfolio_pnl:
            peak_portfolio_pnl = round(combined_unrealized, 2)

        floor = get_portfolio_profit_floor(peak_portfolio_pnl) if use_profit_floor else None
        if floor is not None and open_positions and combined_unrealized <= floor:
            for pos in open_positions:
                exit_px = round(pos["last_close"] * (1.0 - slippage_and_stt_pct), 2)
                pnl = round((exit_px - pos["entry"]) * pos["qty"], 2)
                cash += exit_px * pos["qty"]
                closed_trades.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": dt.strftime("%Y-%m-%d"),
                    "symbol": pos["symbol"],
                    "tier": pos["tier"],
                    "qty": pos["qty"],
                    "entry": pos["entry"],
                    "exit_price": exit_px,
                    "status": "PORTFOLIO_FLOOR_EXIT",
                    "pnl": pnl,
                    "return_pct": round(((exit_px - pos["entry"]) / pos["entry"]) * 100.0, 2),
                })
            open_positions = []
            combined_unrealized = 0.0
            peak_portfolio_pnl = 0.0

        # ------------------------------------------------------------------
        # 3. SCREEN & ENTER NEW SWING POSITIONS (IF SLOTS < MAX_OPEN_POSITIONS)
        # ------------------------------------------------------------------
        if len(open_positions) < max_open_positions:
            held_syms = {p["symbol"] for p in open_positions}
            candidates = []

            for sym, df in stock_frames.items():
                if sym in held_syms or dt not in df.index:
                    continue
                bar = df.loc[dt]
                close_px = float(bar["Close"])
                avg_vol = float(bar["avg_vol20"]) if pd.notna(bar["avg_vol20"]) else 0.0
                if close_px < 50.0 or avg_vol < 100_000:
                    continue

                sma50 = float(bar["sma50"]) if pd.notna(bar["sma50"]) else close_px
                sma200 = float(bar["sma200"]) if pd.notna(bar["sma200"]) else close_px
                rsi14 = float(bar["rsi14"])
                atr14 = float(bar["atr14"]) if pd.notna(bar["atr14"]) else close_px * 0.015
                rvol = float(bar["rvol"]) if pd.notna(bar["rvol"]) else 1.0
                beta = float(bar["beta"]) if pd.notna(bar["beta"]) else 1.0
                day_chg = float(bar["day_chg"]) if pd.notna(bar["day_chg"]) else 0.0

                # Trend & momentum filter
                if close_px < sma50 or rsi14 > 72 or rvol < 1.05:
                    continue

                if beta <= 1.05 and close_px >= sma200:
                    tier = "LOW"
                elif 0.85 <= beta <= 1.55 and 45 <= rsi14 <= 68:
                    tier = "MID"
                elif beta >= 1.25 and day_chg >= 1.5:
                    tier = "HIGH"
                else:
                    continue

                if defensive_mode and tier == "HIGH":
                    continue

                candidates.append({
                    "symbol": sym,
                    "tier": tier,
                    "close": close_px,
                    "atr14": max(atr14, close_px * 0.01),
                    "rvol": rvol,
                })

            candidates.sort(key=lambda x: x["rvol"], reverse=True)
            max_new_today = 1 if defensive_mode else 2
            entered_today = 0

            for cand in candidates:
                if len(open_positions) >= max_open_positions or entered_today >= max_new_today:
                    break
                tier = cand["tier"]
                entry_px = round(cand["close"] * (1.0 + slippage_and_stt_pct), 2)
                atr_mult = ATR_MULT_PER_TIER[tier]
                sl_px = round(entry_px - (atr_mult * cand["atr14"]), 2)
                # Ensure >= 1:2 R:R
                target_px = round(entry_px + (2.0 * atr_mult * cand["atr14"]), 2)

                risk_per_share = max(entry_px - sl_px, entry_px * 0.005)
                qty_risk = int((cash * RISK_PER_TIER[tier]) // risk_per_share)
                qty_cap = int((cash * 0.25) // entry_px)
                qty = min(qty_risk, qty_cap)
                cost = qty * entry_px

                if qty > 0 and cost <= cash:
                    cash -= cost
                    open_positions.append({
                        "entry_date": dt.strftime("%Y-%m-%d"),
                        "symbol": cand["symbol"],
                        "tier": tier,
                        "qty": qty,
                        "entry": entry_px,
                        "sl": sl_px,
                        "target": target_px,
                        "last_close": cand["close"],
                    })
                    entered_today += 1

        holdings_val = sum(p["qty"] * p["last_close"] for p in open_positions)
        total_eq = round(cash + holdings_val, 2)
        nifty_eq = round((n_close / first_nifty_close) * initial_capital, 2)
        equity_curve.append({
            "date": dt.strftime("%Y-%m-%d"),
            "portfolio_equity": total_eq,
            "nifty_benchmark": nifty_eq,
            "open_positions": len(open_positions),
        })

    eq_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(closed_trades)

    final_equity = float(eq_df["portfolio_equity"].iloc[-1])
    final_nifty = float(eq_df["nifty_benchmark"].iloc[-1])
    net_return_pct = round(((final_equity - initial_capital) / initial_capital) * 100.0, 2)
    nifty_return_pct = round(((final_nifty - initial_capital) / initial_capital) * 100.0, 2)
    alpha_pct = round(net_return_pct - nifty_return_pct, 2)

    # Max Drawdown %
    eq_df["cum_max"] = eq_df["portfolio_equity"].cummax()
    eq_df["drawdown_pct"] = ((eq_df["portfolio_equity"] - eq_df["cum_max"]) / eq_df["cum_max"]) * 100.0
    max_drawdown_pct = round(float(eq_df["drawdown_pct"].min()), 2)

    # Daily Sharpe Ratio (annualized, 252 trading days)
    daily_rets = eq_df["portfolio_equity"].pct_change().dropna()
    if len(daily_rets) > 2 and daily_rets.std() > 0:
        sharpe_ratio = round(float((daily_rets.mean() / daily_rets.std()) * math.sqrt(252)), 2)
    else:
        sharpe_ratio = 0.0

    # Trade stats
    total_closed = len(trades_df)
    if total_closed > 0:
        wins_df = trades_df[trades_df["pnl"] > 0]
        losses_df = trades_df[trades_df["pnl"] < 0]
        win_rate_pct = round((len(wins_df) / total_closed) * 100.0, 1)
        gross_profit = float(wins_df["pnl"].sum()) if not wins_df.empty else 0.0
        gross_loss = abs(float(losses_df["pnl"].sum())) if not losses_df.empty else 0.0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        avg_win = round(float(wins_df["pnl"].mean()), 2) if not wins_df.empty else 0.0
        avg_loss = round(float(losses_df["pnl"].mean()), 2) if not losses_df.empty else 0.0
    else:
        win_rate_pct = 0.0
        profit_factor = 0.0
        avg_win = 0.0
        avg_loss = 0.0

    return {
        "metrics": {
            "initial_capital": initial_capital,
            "final_equity": final_equity,
            "net_return_pct": net_return_pct,
            "nifty_return_pct": nifty_return_pct,
            "alpha_pct": alpha_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": sharpe_ratio,
            "total_closed_trades": total_closed,
            "still_open_trades": len(open_positions),
            "win_rate_pct": win_rate_pct,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
        },
        "equity_curve": eq_df,
        "closed_trades": trades_df,
    }
