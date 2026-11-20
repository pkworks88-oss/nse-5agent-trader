import os
import json
import math
import time
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import numpy as np
import pandas as pd
from scipy.stats import norm
import yfinance as yf
import feedparser
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
from google.genai.errors import APIError
from schemas import IntradayEquityDecision, IntradayFnODecision, UnifiedIntradayCommitteeOutput
from swing_engine import get_gsheet_client, SWING_SHEET_NAME
import sys
from pathlib import Path
_ENGINE_DIR = str(Path(__file__).resolve().parent)
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)
try:
    from skills.market_skills import evaluate_intraday_chop_skill, get_sector, can_add_sector_position, compute_rsi_series, compute_pdh_pdl
except ModuleNotFoundError:
    from market_skills import evaluate_intraday_chop_skill, get_sector, can_add_sector_position, compute_rsi_series, compute_pdh_pdl

MIN_OPTION_PREMIUM = 15.0
MAX_CONSECUTIVE_LOSSES_PER_BOOK = 2
MIN_ADX_FOR_TREND = 22.0
MAX_MIS_RSI_FOR_BUY = 75.0

IST = timezone(timedelta(hours=5, minutes=30))
CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]
INTRADAY_SHEET_NAME = "NSE_Unified_2L_Ledger"

# --- RING-FENCED ₹2,00,000 DUAL WALLET CONSTANTS ---
INIT_EQ_CAPITAL = 100_000.0
INIT_FNO_CAPITAL = 100_000.0
EQ_MIS_LEVERAGE = 5.0
MAX_EQ_POSITIONS = 3
MAX_FNO_POSITIONS = 2

EQ_RISK_PER_TRADE = 550.0
FNO_RISK_PER_TRADE = 2_200.0
EQ_DAILY_LOSS_LIMIT = -1_500.0
FNO_DAILY_LOSS_LIMIT = -2_000.0

EQ_ROUND_TRIP_COST_PCT = 0.0006
FNO_ROUND_TRIP_FEE_PER_LOT = 55.0

LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30}
STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100}

FNO_LIQUID_STOCKS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS", "SBIN.NS", "AXISBANK.NS",
    "KOTAKBANK.NS", "BHARTIARTL.NS", "LT.NS", "BAJFINANCE.NS", "MARUTI.NS", "M&M.NS", "TATAMOTORS.NS",
    "SUNPHARMA.NS", "TITAN.NS", "NTPC.NS", "POWERGRID.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS",
    "ADANIENT.NS", "ADANIPORTS.NS", "ONGC.NS", "COALINDIA.NS", "BPCL.NS", "HAL.NS", "BEL.NS",
    "TRENT.NS", "DLF.NS", "INDIGO.NS", "TECHM.NS", "WIPRO.NS", "HCLTECH.NS", "ULTRACEMCO.NS",
    "ASIANPAINT.NS", "ITC.NS", "HINDUNILVR.NS", "BAJAJFINSV.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
    "TVSMOTOR.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS", "APOLLOHOSP.NS", "BANKBARODA.NS",
    "PNB.NS", "CANBK.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "RECLTD.NS", "PFC.NS", "IRCTC.NS",
    "BHEL.NS", "VEDL.NS", "POLYCAB.NS", "ABB.NS", "SIEMENS.NS", "CUMMINSIND.NS", "TATAPOWER.NS"
]


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def is_nse_open_now() -> bool:
    dt = now_ist()
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


def log_intraday_msg(sh, cycle_id: str, sender: str, recipient: str, msg_type: str, summary: str, payload):
    try:
        ws = sh.worksheet("agent_messages")
        ws.append_row([
            cycle_id,
            now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
            sender,
            recipient,
            msg_type,
            summary,
            json.dumps(payload),
        ])
    except Exception:
        pass


def ensure_intraday_ledger_initialized(sh):
    """Ensures NSE_Unified_2L_Ledger has valid account, trades, and agent_messages headers and daily reset."""
    today_str = now_ist().strftime("%Y-%m-%d")
    try:
        acct_ws = sh.worksheet("account")
    except Exception:
        acct_ws = sh.add_worksheet(title="account", rows=10, cols=6)
    if acct_ws.acell("A1").value != "active_date":
        acct_ws.update("A1:E2", [
            ["active_date", "eq_cash", "fno_cash", "combined_peak_pnl", "locked_floor"],
            [today_str, INIT_EQ_CAPITAL, INIT_FNO_CAPITAL, 0.0, 0.0],
        ])
    elif acct_ws.acell("A2").value != today_str:
        # New trading day: reset daily peak and floor while keeping cash balances
        eq_c = float(acct_ws.acell("B2").value or INIT_EQ_CAPITAL)
        fno_c = float(acct_ws.acell("C2").value or INIT_FNO_CAPITAL)
        acct_ws.update("A2:E2", [[today_str, eq_c, fno_c, 0.0, 0.0]])

    try:
        trades_ws = sh.worksheet("trades")
    except Exception:
        trades_ws = sh.add_worksheet(title="trades", rows=500, cols=15)
    expected_cols = [
        "timestamp", "date", "book", "symbol", "strategy", "status",
        "qty_or_lots", "lot_size", "entry", "sl", "target", "exit_price", "net_pnl", "reasoning",
        "side", "initial_sl", "gross_pnl", "charges_slippage"
    ]
    if trades_ws.acell("A1").value != "timestamp":
        trades_ws.update("A1:R1", [expected_cols])
    elif trades_ws.acell("O1").value != "side":
        trades_ws.update("O1:R1", [["side", "initial_sl", "gross_pnl", "charges_slippage"]])

    try:
        msg_ws = sh.worksheet("agent_messages")
    except Exception:
        msg_ws = sh.add_worksheet(title="agent_messages", rows=1000, cols=8)
    if msg_ws.acell("A1").value != "cycle_id":
        msg_ws.update("A1:G1", [[
            "cycle_id", "timestamp", "sender", "recipient", "msg_type", "summary", "payload_json"
        ]])


# ==============================================================================
# BLACK-SCHOLES EUROPEAN OPTION PRICING & GREEKS + 11-STRIKE CHAIN BUILDER
# ==============================================================================
def black_scholes_price(spot: float, strike: float, t_years: float, r: float, sigma: float, opt_type: str) -> float:
    t = max(t_years, 1e-5)
    vol = max(sigma, 0.05)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if opt_type == "CE":
        px = spot * norm.cdf(d1) - strike * math.exp(-r * t) * norm.cdf(d2)
    else:
        px = strike * math.exp(-r * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    return round(max(px, 1.5), 2)


def black_scholes_delta(spot: float, strike: float, t_years: float, r: float, sigma: float, opt_type: str) -> float:
    t = max(t_years, 1e-5)
    vol = max(sigma, 0.05)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    return round(float(norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1.0), 3)


def days_to_next_tuesday() -> float:
    dt = now_ist()
    # Tuesday = 1
    days_ahead = (1 - dt.weekday()) % 7
    if days_ahead == 0 and dt.hour >= 15:
        days_ahead = 7
    return max(0.35, float(days_ahead) + 0.5)


def fetch_index_fno_context() -> dict:
    """Fetches NIFTY & BANKNIFTY spot, India VIX, intraday momentum, and 11-strike option chains."""
    context = {
        "india_vix": 14.5,
        "vix_regime": "LOW_VIX_SINGLE_LEG",
        "indices": {},
    }
    try:
        raw = yf.download(["^NSEI", "^NSEBANK", "^INDIAVIX"], period="5d", interval="15m", group_by="ticker", progress=False, threads=True)
        if "^INDIAVIX" in raw.columns.get_level_values(0):
            v_close = raw["^INDIAVIX"]["Close"].dropna()
            if not v_close.empty:
                vix_val = round(float(v_close.iloc[-1]), 2)
                context["india_vix"] = vix_val
                context["vix_regime"] = "DEBIT_SPREAD_PREFERRED" if vix_val >= 17.0 else "SINGLE_LEG_BUY_ALLOWED"

        sigma = max(0.11, min(0.35, context["india_vix"] / 100.0))
        t_nifty = days_to_next_tuesday() / 365.0
        t_bnifty = 14.0 / 365.0

        for idx_name, yahoo_sym, t_yr in [("NIFTY", "^NSEI", t_nifty), ("BANKNIFTY", "^NSEBANK", t_bnifty)]:
            if yahoo_sym not in raw.columns.get_level_values(0):
                continue
            sub = raw[yahoo_sym].dropna()
            if len(sub) < 5:
                continue
            spot = round(float(sub["Close"].iloc[-1]), 2)
            day_open = float(sub["Open"].iloc[-min(25, len(sub))])
            chg_pct = round(((spot - day_open) / day_open) * 100.0, 2)
            step = STRIKE_STEPS[idx_name]
            atm = int(round(spot / step) * step)

            # Synthetic PCR proxy from intraday price-volume balance
            pcr = round(max(0.65, min(1.45, 1.0 + (chg_pct * 0.12))), 2)

            chain = []
            for offset in range(-5, 6):
                strike = atm + (offset * step)
                ce_px = black_scholes_price(spot, strike, t_yr, 0.065, sigma, "CE")
                pe_px = black_scholes_price(spot, strike, t_yr, 0.065, sigma, "PE")
                ce_delta = black_scholes_delta(spot, strike, t_yr, 0.065, sigma, "CE")
                pe_delta = black_scholes_delta(spot, strike, t_yr, 0.065, sigma, "PE")
                chain.append({
                    "strike": strike,
                    "moneyness": "ATM" if offset == 0 else (f"OTM+{offset}" if offset > 0 else f"ITM{offset}"),
                    "ce_ltp": ce_px,
                    "ce_delta": ce_delta,
                    "pe_ltp": pe_px,
                    "pe_delta": pe_delta,
                })

            chop_skill = evaluate_intraday_chop_skill(sub, min_adx=MIN_ADX_FOR_TREND)
            context["indices"][idx_name] = {
                "spot": spot,
                "intraday_chg_pct": chg_pct,
                "atm_strike": atm,
                "lot_size": LOT_SIZES[idx_name],
                "pcr": pcr,
                "adx_14": chop_skill["adx"],
                "rsi_14": chop_skill["rsi_14"],
                "rsi_exhaustion": chop_skill["rsi_exhaustion"],
                "pdh": chop_skill["pdh"],
                "pdl": chop_skill["pdl"],
                "dist_to_pdh_pct": chop_skill["dist_to_pdh_pct"],
                "is_trending": chop_skill["is_trending"],
                "orb_signal": chop_skill["orb_signal"],
                "directional_bias": chop_skill["directional_bias"],
                "chain": chain,
            }
    except Exception:
        pass
    return context


# ==============================================================================
# INTRADAY 61-STOCK MIS FUNNEL (AG1 LOW / AG2 MID / AG3 HIGH)
# ==============================================================================
def fetch_equity_metrics(excluded_symbols: set[str] | None = None) -> dict[str, list[dict]]:
    excluded = excluded_symbols or set()
    buckets = {"AG1_LOW_RISK": [], "AG2_MID_RISK": [], "AG3_HIGH_RISK": []}
    try:
        data = yf.download(FNO_LIQUID_STOCKS, period="5d", interval="5m", group_by="ticker", progress=False, threads=True)
    except Exception:
        return buckets

    for sym in FNO_LIQUID_STOCKS:
        if sym in excluded:
            continue
        try:
            if sym not in data.columns.get_level_values(0):
                continue
            sub = data[sym].dropna()
            if len(sub) < 25:
                continue

            close = sub["Close"]
            ltp = float(close.iloc[-1])
            ema9 = float(close.ewm(span=9).mean().iloc[-1])
            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            atr = float((sub["High"] - sub["Low"]).rolling(14).mean().iloc[-1])

            today_bars = sub.iloc[-min(25, len(sub)):]
            typ_px = (today_bars["High"] + today_bars["Low"] + today_bars["Close"]) / 3.0
            vwap = float((typ_px * today_bars["Volume"]).sum() / max(today_bars["Volume"].sum(), 1.0))
            day_open = float(today_bars["Open"].iloc[0])
            chg_pct = round(((ltp - day_open) / day_open) * 100.0, 2)

            avg_vol = float(sub["Volume"].rolling(20).mean().iloc[-1])
            rvol = round(float(sub["Volume"].iloc[-1] / max(avg_vol, 1.0)), 2)

            if ltp < vwap or ema9 < ema20:
                continue

            rsi_s = compute_rsi_series(close, period=14).dropna()
            rsi_val = round(float(rsi_s.iloc[-1]), 2) if not rsi_s.empty else 50.0
            if rsi_val > MAX_MIS_RSI_FOR_BUY:
                continue
            pd_lvl = compute_pdh_pdl(sub)

            item = {
                "symbol": sym,
                "ltp": round(ltp, 2),
                "vwap": round(vwap, 2),
                "ema9": round(ema9, 2),
                "ema20": round(ema20, 2),
                "atr": round(max(atr, ltp * 0.004), 2),
                "rsi_14": rsi_val,
                "pdh": pd_lvl["pdh"],
                "pdl": pd_lvl["pdl"],
                "dist_to_pdh_pct": pd_lvl["dist_to_pdh_pct"],
                "chg_pct": chg_pct,
                "rvol": rvol,
            }

            if 0.2 <= chg_pct <= 1.5 and rvol >= 1.0:
                buckets["AG1_LOW_RISK"].append(item)
            elif 0.5 <= chg_pct <= 3.5 and rvol >= 1.1:
                buckets["AG2_MID_RISK"].append(item)
            elif rvol >= 1.3 and abs(chg_pct) >= 1.2:
                buckets["AG3_HIGH_RISK"].append(item)
        except Exception:
            continue

    for k in buckets:
        buckets[k] = sorted(buckets[k], key=lambda x: x["rvol"], reverse=True)[:5]
    return buckets


def get_swing_open_symbols(gcp_creds_dict: dict) -> set[str]:
    """Cross-Desk Anti-Double-Exposure Check: reads open symbols from ₹10L Swing Ledger."""
    try:
        swing_sh = get_gsheet_client(gcp_creds_dict, SWING_SHEET_NAME)
        rows = swing_sh.worksheet("trades").get_all_records()
        return {r["symbol"] for r in rows if r.get("status") == "OPEN"}
    except Exception:
        return set()


# ==============================================================================
# TWO-TIER STEPPED PROFIT RATCHET & 20-SECOND WATCHDOG LOOP
# ==============================================================================
def get_intraday_stepped_floor(peak_pnl: float) -> float:
    if peak_pnl >= 5_000.0:
        return round(peak_pnl * 0.75, 2)
    elif peak_pnl >= 4_000.0:
        return 3_000.0
    elif peak_pnl >= 3_000.0:
        return 1_500.0
    return 0.0


def parse_fno_symbol(sym: str) -> dict | None:
    # Format: NIFTY_25600_CE or NIFTY_25600_25800_CE_SPREAD
    parts = sym.split("_")
    if len(parts) < 3 or parts[0] not in ("NIFTY", "BANKNIFTY"):
        return None
    idx_name = parts[0]
    opt_type = "CE" if "CE" in parts else "PE"
    strikes = [int(p) for p in parts if p.isdigit()]
    if not strikes:
        return None
    return {
        "index_name": idx_name,
        "strike": strikes[0],
        "hedge_strike": strikes[1] if len(strikes) > 1 else 0,
        "opt_type": opt_type,
    }



MIN_INTRADAY_RR = 1.60


def validate_entry_rr(entry: float, initial_sl: float, target: float, min_rr: float = MIN_INTRADAY_RR) -> tuple[bool, float, str]:
    if not (initial_sl < entry < target):
        return False, 0.0, f"Invalid bracket order: initial_sl ({initial_sl}) < entry ({entry}) < target ({target}) violated"
    risk = entry - initial_sl
    reward = target - entry
    if risk <= 0:
        return False, 0.0, "Zero/negative initial risk"
    rr = round(reward / risk, 2)
    if rr < min_rr:
        return False, rr, f"R:R {rr:.2f} below minimum {min_rr:.2f}"
    return True, rr, f"R:R 1:{rr:.2f} OK"


def compute_intraday_target_trail_sl(entry: float, target: float, peak_high: float, book: str = "EQUITY") -> tuple[float | None, str]:
    """
    Progressive target-achieved trailing SL for Intraday MIS & Index F&O:
      - Once 20% of target move is achieved (checked at >=20% / >20%): moves SL to breakeven buffer
      - At 40% target achieved: locks 20% of target distance (Entry + 0.20 * dist)
      - At 60% target achieved: locks 40% of target distance (Entry + 0.40 * dist)
      - At 80% target achieved: locks 65% of target distance (Entry + 0.65 * dist)
    """
    dist = max(target - entry, 0.5)
    gain = peak_high - entry
    prog = gain / dist
    if prog < 0.20:
        return None, ""
    if prog >= 0.80:
        return round(entry + 0.65 * dist, 2), "80% Target -> Locked 65% of Target Move"
    if prog >= 0.60:
        return round(entry + 0.40 * dist, 2), "60% Target -> Locked 40% of Target Move"
    if prog >= 0.40:
        return round(entry + 0.20 * dist, 2), "40% Target -> Locked 20% of Target Move"
    be_sl = round(entry * 1.001 if book == "EQUITY" else entry + 1.5, 2)
    return be_sl, "20% Target -> Breakeven Buffer"


def evaluate_exits_and_trailing_sl(sh, tick_id: str, force_square_off: bool = False):
    acct_ws = sh.worksheet("account")
    trades_ws = sh.worksheet("trades")
    records = trades_ws.get_all_records()

    open_rows = [(idx, row) for idx, row in enumerate(records, start=2) if row.get("status") == "OPEN"]
    if not open_rows:
        return

    eq_syms = list({row["symbol"] for _, row in open_rows if row.get("book") == "EQUITY"})
    has_fno = any(row.get("book") == "FNO" for _, row in open_rows)

    eq_quotes = {}
    if eq_syms:
        try:
            q_data = yf.download(eq_syms, period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
            for s in eq_syms:
                sub = q_data[s].dropna() if len(eq_syms) > 1 else q_data.dropna()
                if not sub.empty:
                    bar = sub.iloc[-1]
                    eq_quotes[s] = {
                        "close": round(float(bar["Close"]), 2),
                        "high": round(float(bar["High"]), 2),
                        "low": round(float(bar["Low"]), 2),
                    }
        except Exception:
            pass

    spot_map = {}
    sigma = 0.145
    if has_fno:
        try:
            idx_data = yf.download(["^NSEI", "^NSEBANK", "^INDIAVIX"], period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
            if "^INDIAVIX" in idx_data.columns.get_level_values(0):
                v_s = idx_data["^INDIAVIX"]["Close"].dropna()
                if not v_s.empty:
                    sigma = max(0.11, min(0.35, float(v_s.iloc[-1]) / 100.0))
            for k, y_sym in [("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")]:
                if y_sym in idx_data.columns.get_level_values(0):
                    s_df = idx_data[y_sym].dropna()
                    if not s_df.empty:
                        b = s_df.iloc[-1]
                        spot_map[k] = {"close": float(b["Close"]), "high": float(b["High"]), "low": float(b["Low"])}
        except Exception:
            pass

    eq_cash = float(acct_ws.acell("B2").value or INIT_EQ_CAPITAL)
    fno_cash = float(acct_ws.acell("C2").value or INIT_FNO_CAPITAL)
    peak_pnl = float(acct_ws.acell("D2").value or 0.0)
    locked_floor = float(acct_ws.acell("E2").value or 0.0)

    cash_dirty = False
    remaining_open = []
    combined_unrealized = 0.0

    for idx, row in open_rows:
        book = str(row.get("book", "EQUITY"))
        sym = str(row["symbol"])
        qty_or_lots = int(row["qty_or_lots"])
        lot_size = int(row.get("lot_size", 1) or 1)
        entry = float(row["entry"])
        sl = float(row["sl"])
        target = float(row["target"])
        target_dist = max(target - entry, 0.5)

        if book == "EQUITY":
            q = eq_quotes.get(sym)
            if not q:
                continue
            ltp, b_high, b_low = q["close"], q["high"], q["low"]
            units = qty_or_lots
            fee_buf = round((entry + ltp) * units * EQ_ROUND_TRIP_COST_PCT, 2)
        else:
            parsed = parse_fno_symbol(sym)
            if not parsed or parsed["index_name"] not in spot_map:
                continue
            sp = spot_map[parsed["index_name"]]
            t_yr = (days_to_next_tuesday() if parsed["index_name"] == "NIFTY" else 14.0) / 365.0
            opt_t = parsed["opt_type"]
            k1, k2 = parsed["strike"], parsed["hedge_strike"]

            def calc_opt_prem(s_px: float) -> float:
                p1 = black_scholes_price(s_px, k1, t_yr, 0.065, sigma, opt_t)
                if k2 > 0:
                    p2 = black_scholes_price(s_px, k2, t_yr, 0.065, sigma, opt_t)
                    return round(max(p1 - p2, 1.0), 2)
                return p1

            ltp = calc_opt_prem(sp["close"])
            p_high = calc_opt_prem(sp["high"] if opt_t == "CE" else sp["low"])
            p_low = calc_opt_prem(sp["low"] if opt_t == "CE" else sp["high"])
            b_high, b_low = max(ltp, p_high), min(ltp, p_low)
            units = qty_or_lots * lot_size
            fee_buf = round(qty_or_lots * FNO_ROUND_TRIP_FEE_PER_LOT, 2)

        # Forced EOD / Emergency Square-Off
        if force_square_off:
            gross_pnl = round((ltp - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            if book == "EQUITY":
                eq_cash += ((entry * units) / EQ_MIS_LEVERAGE) + net_pnl
            else:
                fno_cash += (entry * units) + net_pnl
            cash_dirty = True
            trades_ws.update(f"F{idx}", [["EOD_SQUAREOFF"]])
            trades_ws.update(f"L{idx}:M{idx}", [[ltp, net_pnl]])
            trades_ws.update(f"Q{idx}:R{idx}", [[gross_pnl, fee_buf]])
            record_intraday_lesson(sh, tick_id, book, sym, str(row.get("strategy", "")), "EOD_SQUAREOFF", net_pnl, str(row.get("reasoning", "")))
            log_intraday_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "EOD_SQUAREOFF",
                f"⏰ EOD Square-Off [{book}] {sym} @ ₹{ltp} | Net P&L: ₹{net_pnl:+,.2f}",
                {"book": book, "symbol": sym, "exit_price": ltp, "net_pnl": net_pnl}
            )
            continue

        # Stop-Loss Exit (clamped cleanly for trailed stops above entry)
        if b_low <= sl or ltp <= sl:
            if sl >= entry:
                exit_px = round(max(sl * (1.0 - TRAIL_EXIT_SLIPPAGE_PCT), min(ltp, sl)), 2)
            else:
                exit_px = round(min(ltp, sl) if b_low <= sl else ltp, 2)
            gross_pnl = round((exit_px - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            if book == "EQUITY":
                eq_cash += ((entry * units) / EQ_MIS_LEVERAGE) + net_pnl
            else:
                fno_cash += (entry * units) + net_pnl
            cash_dirty = True
            tag = "TRAILING_SL_HIT" if sl >= entry else "SL_HIT"
            trades_ws.update(f"F{idx}", [[tag]])
            trades_ws.update(f"L{idx}:M{idx}", [[exit_px, net_pnl]])
            trades_ws.update(f"Q{idx}:R{idx}", [[gross_pnl, fee_buf]])
            record_intraday_lesson(sh, tick_id, book, sym, str(row.get("strategy", "")), tag, net_pnl, str(row.get("reasoning", "")))
            log_intraday_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", tag,
                f"🛑 [{book}] {sym} ({tag}) @ ₹{exit_px} | Net P&L: ₹{net_pnl:+,.2f}",
                {"book": book, "symbol": sym, "exit_price": exit_px, "net_pnl": net_pnl}
            )
            continue

        # Target Hit Exit
        if b_high >= target or ltp >= target:
            exit_px = round(max(ltp, target), 2)
            gross_pnl = round((exit_px - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            if book == "EQUITY":
                eq_cash += ((entry * units) / EQ_MIS_LEVERAGE) + net_pnl
            else:
                fno_cash += (entry * units) + net_pnl
            cash_dirty = True
            trades_ws.update(f"F{idx}", [["TARGET_HIT"]])
            trades_ws.update(f"L{idx}:M{idx}", [[exit_px, net_pnl]])
            trades_ws.update(f"Q{idx}:R{idx}", [[gross_pnl, fee_buf]])
            record_intraday_lesson(sh, tick_id, book, sym, str(row.get("strategy", "")), "TARGET_HIT", net_pnl, str(row.get("reasoning", "")))
            log_intraday_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "TARGET_HIT",
                f"🎯 [{book}] {sym} HIT TARGET @ ₹{exit_px} | Net P&L: ₹{net_pnl:+,.2f}",
                {"book": book, "symbol": sym, "exit_price": exit_px, "net_pnl": net_pnl}
            )
            continue

        # Progressive Target-Achieved Trailing SL (Starts at 20% Target & Trails Upward)
        gain_peak = b_high - entry
        prog = gain_peak / target_dist
        cand_sl, trail_lbl = compute_intraday_target_trail_sl(entry, target, b_high, book=book)

        if cand_sl is not None and cand_sl > sl:
            trades_ws.update(f"J{idx}", [[cand_sl]])
            sl = cand_sl
            log_intraday_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "TRAILING_SL",
                f"🛡️ [{book}] {sym}: {trail_lbl} (SL raised to ₹{cand_sl} at {int(round(prog * 100))}% target progress)",
                {"book": book, "symbol": sym, "new_sl": cand_sl, "progress_pct": round(prog * 100, 1)}
            )

        unreal = ((ltp - entry) * units) - fee_buf
        combined_unrealized += unreal
        remaining_open.append((idx, row, book, sym, units, entry, ltp, fee_buf))

    # Stepped Portfolio Floor (+₹2k -> +₹1k floor; +₹3k -> +₹2k floor; >=+₹4k -> 75% peak)
    if combined_unrealized > peak_pnl:
        peak_pnl = round(combined_unrealized, 2)
        locked_floor = get_intraday_stepped_floor(peak_pnl)
        cash_dirty = True

    if locked_floor > 0 and remaining_open and combined_unrealized <= locked_floor:
        for idx, row, book, sym, units, entry, ltp, fee_buf in remaining_open:
            gross_pnl = round((ltp - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            if book == "EQUITY":
                eq_cash += ((entry * units) / EQ_MIS_LEVERAGE) + net_pnl
            else:
                fno_cash += (entry * units) + net_pnl
            trades_ws.update(f"F{idx}", [["STEPPED_FLOOR_EXIT"]])
            trades_ws.update(f"L{idx}:M{idx}", [[ltp, net_pnl]])
            trades_ws.update(f"Q{idx}:R{idx}", [[gross_pnl, fee_buf]])
            record_intraday_lesson(sh, tick_id, book, sym, str(row.get("strategy", "")), "STEPPED_FLOOR_EXIT", net_pnl, str(row.get("reasoning", "")))
        log_intraday_msg(
            sh, tick_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "STEPPED_FLOOR_EXIT",
            f"💰 Intraday Stepped Floor triggered (Peak ₹{peak_pnl:+,.2f} -> Floor ₹{locked_floor:+,.2f}) — closed {len(remaining_open)} legs.",
            {"peak_pnl": peak_pnl, "locked_floor": locked_floor}
        )
        peak_pnl = 0.0
        locked_floor = 0.0
        cash_dirty = True

    if cash_dirty:
        today_str = now_ist().strftime("%Y-%m-%d")
        acct_ws.update("A2:E2", [[today_str, round(eq_cash, 2), round(fno_cash, 2), round(peak_pnl, 2), round(locked_floor, 2)]])





CAS_DEC7_2026_ENABLED = False
TRAIL_EXIT_SLIPPAGE_PCT = 0.0005


def get_eod_squareoff_minutes(cas_dec7_enabled: bool = CAS_DEC7_2026_ENABLED) -> int:
    # Pre-Dec 7, 2026: 15:15 IST (915 mins); From Dec 7, 2026 CAS shift (3:30 PM CAS): 15:25 IST (925 mins) buffer before 3:30 PM
    return (15 * 60 + 25) if cas_dec7_enabled else (15 * 60 + 15)


def compute_book_and_combined_trust(intraday_records: list[dict], swing_records: list[dict] | None = None) -> dict:
    """Computes individual trust scores (EQUITY_MIS, FNO, SWING) and combined score per agent."""
    agents = ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]
    books = ["EQUITY_MIS", "FNO", "SWING"]
    by_book = {b: {a: 50.0 for a in agents} for b in books}

    for r in (intraday_records or []):
        if r.get("status") == "OPEN":
            continue
        raw_book = str(r.get("book", "EQUITY")).upper()
        b_key = "FNO" if raw_book == "FNO" else "EQUITY_MIS"
        txt = f"{r.get('reasoning', '')} {r.get('strategy', '')}"
        pnl = float(r.get("net_pnl", 0) or 0)
        for a in agents:
            if a in txt or (b_key == "EQUITY_MIS" and a.split("_")[1] in str(r.get("strategy", "")).upper()):
                delta = 2.5 if pnl > 0 else (-3.0 if pnl < 0 else 0.0)
                by_book[b_key][a] = max(35.0, min(98.0, by_book[b_key][a] + delta))

    for r in (swing_records or []):
        if r.get("status") == "OPEN":
            continue
        txt = f"{r.get('reason', '')} {r.get('reasoning', '')} {r.get('tier', '')}"
        pnl = float(r.get("pnl", r.get("net_pnl", 0)) or 0)
        for a in agents:
            if a in txt or (a.split("_")[1] in str(r.get("tier", "")).upper()):
                delta = 2.5 if pnl > 0 else (-3.0 if pnl < 0 else 0.0)
                by_book["SWING"][a] = max(35.0, min(98.0, by_book["SWING"][a] + delta))

    combined = {}
    for a in agents:
        c_val = (by_book["SWING"][a] * 0.40) + (by_book["EQUITY_MIS"][a] * 0.30) + (by_book["FNO"][a] * 0.30)
        combined[a] = int(round(c_val))

    rounded_books = {
        b: {a: int(round(by_book[b][a])) for a in agents}
        for b in books
    }
    return {
        "EQUITY_MIS": rounded_books["EQUITY_MIS"],
        "FNO": rounded_books["FNO"],
        "SWING": rounded_books["SWING"],
        "COMBINED": combined,
    }


def log_shadow_skip(sh, cycle_id: str, book: str, symbol: str, reason: str, ref_price: float = 0.0):
    log_intraday_msg(
        sh,
        cycle_id,
        "AG4_CHIEF_ANALYST",
        "SHADOW_SKIP_LOG",
        "SHADOW_SKIP",
        f"⏭️ [SHADOW_SKIP | {book}] {symbol} @ ₹{ref_price:.2f} — {reason}",
        {"book": book, "symbol": symbol, "reason": reason, "ref_price": ref_price},
    )


def compute_intraday_agent_trust(records: list[dict]) -> dict[str, int]:
    """Computes dynamic 0-100 trust scores per agent from closed intraday/F&O trades."""
    scores = {"AG1_LOW_RISK": 50, "AG2_MID_RISK": 50, "AG3_HIGH_RISK": 50}
    for r in records:
        if r.get("status") == "OPEN":
            continue
        reason_txt = str(r.get("reasoning", ""))
        pnl = float(r.get("net_pnl", 0) or 0)
        for ag_key in list(scores.keys()):
            if ag_key in reason_txt:
                delta = 2.5 if pnl > 0 else (-3.0 if pnl < 0 else 0.0)
                scores[ag_key] = int(max(35, min(98, round(scores[ag_key] + delta))))
    return scores


def record_intraday_lesson(sh, cycle_id: str, book: str, symbol: str, strategy: str, exit_tag: str, net_pnl: float, reasoning: str):
    """Logs a structured AGENT_LESSON takeaway in NSE_Unified_2L_Ledger on trade close."""
    outcome = "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "FLAT")
    if outcome == "WIN":
        takeaway = f"Confirmed momentum ({strategy} / {exit_tag}) delivered ₹{net_pnl:+,.2f} on {symbol}; keep trend & volume discipline."
    elif exit_tag == "STEPPED_FLOOR_EXIT":
        takeaway = f"Portfolio floor pullback on {symbol} ({net_pnl:+,.2f}); avoid chasing late entries into mean reversion."
    elif "PE" in symbol and net_pnl < 0:
        takeaway = f"Put leg {symbol} stopped out ({net_pnl:+,.2f}); require stronger downside VWAP/ADX confirmation before PE buys."
    elif "CE" in symbol and net_pnl < 0:
        takeaway = f"Call leg {symbol} stopped out ({net_pnl:+,.2f}); avoid CE buys in flat/choppy index sessions."
    else:
        takeaway = f"{book} {symbol} exited via {exit_tag} ({net_pnl:+,.2f}); tighten entry selectivity in noisy tape."

    payload = {
        "book": book,
        "symbol": symbol,
        "strategy": strategy,
        "exit_tag": exit_tag,
        "net_pnl": round(float(net_pnl), 2),
        "outcome": outcome,
        "source_tag": reasoning[:90],
    }
    log_intraday_msg(
        sh,
        cycle_id,
        "AG4_CHIEF_ANALYST",
        "INTRADAY_MEMORY",
        "AGENT_LESSON",
        f"🧠 [{outcome} | {book}] {symbol}: {takeaway}",
        payload,
    )


def get_recent_intraday_lessons(msg_records: list[dict], limit: int = 5) -> list[str]:
    lessons = []
    for m in reversed(msg_records or []):
        if str(m.get("msg_type", "")) == "AGENT_LESSON":
            lessons.append(str(m.get("summary", "")))
            if len(lessons) >= limit:
                break
    return lessons


def has_loss_streak(records: list[dict], today_str: str, book: str, max_streak: int = MAX_CONSECUTIVE_LOSSES_PER_BOOK) -> bool:
    closed_book = [
        r for r in records
        if str(r.get("date")) == today_str and r.get("status") != "OPEN" and r.get("book") == book
    ]
    if len(closed_book) < max_streak:
        return False
    streak = 0
    for r in reversed(closed_book):
        if float(r.get("net_pnl", 0) or 0) < 0:
            streak += 1
            if streak >= max_streak:
                return True
        else:
            break
    return False

# ==============================================================================
# UNIFIED 5-MINUTE INTRADAY CYCLE (EQUITY MIS + NIFTY/BANKNIFTY F&O)
# ==============================================================================
async def run_unified_cycle(api_key: str, gcp_creds_dict: dict, force_square_off: bool = False):
    sh = get_gsheet_client(gcp_creds_dict, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(sh)
    cycle_id = now_ist().strftime("INTRA-%H%M%S-IST")

    dt = now_ist()
    mins = dt.hour * 60 + dt.minute
    if force_square_off or mins >= get_eod_squareoff_minutes(CAS_DEC7_2026_ENABLED):
        evaluate_exits_and_trailing_sl(sh, cycle_id, force_square_off=True)
        return

    # Always run exit/trailing check first
    evaluate_exits_and_trailing_sl(sh, cycle_id, force_square_off=False)

    # Stop new entries after 02:00 PM IST
    if mins >= (14 * 60):
        return

    acct_ws = sh.worksheet("account")
    trades_ws = sh.worksheet("trades")
    records = trades_ws.get_all_records()
    today_str = dt.strftime("%Y-%m-%d")

    today_closed = [r for r in records if str(r.get("date")) == today_str and r.get("status") != "OPEN"]
    eq_day_pnl = sum(float(r.get("net_pnl", 0) or 0) for r in today_closed if r.get("book") == "EQUITY")
    fno_day_pnl = sum(float(r.get("net_pnl", 0) or 0) for r in today_closed if r.get("book") == "FNO")

    open_eq = [r for r in records if r.get("status") == "OPEN" and r.get("book") == "EQUITY"]
    open_fno = [r for r in records if r.get("status") == "OPEN" and r.get("book") == "FNO"]

    eq_streak_stop = has_loss_streak(records, today_str, "EQUITY")
    fno_streak_stop = has_loss_streak(records, today_str, "FNO")
    eq_halted = (eq_day_pnl <= EQ_DAILY_LOSS_LIMIT) or (len(open_eq) >= MAX_EQ_POSITIONS) or eq_streak_stop
    fno_halted = (fno_day_pnl <= FNO_DAILY_LOSS_LIMIT) or (len(open_fno) >= MAX_FNO_POSITIONS) or fno_streak_stop
    # Tuesday expiry rule: no naked F&O buying after 1:30 PM IST
    if dt.weekday() == 1 and mins >= (13 * 60 + 30):
        fno_halted = True

    if eq_halted and fno_halted:
        return

    # Cross-Desk Anti-Double-Exposure: exclude symbols already held in ₹10L Swing Desk
    swing_held = get_swing_open_symbols(gcp_creds_dict)
    intraday_held = {r["symbol"] for r in open_eq}
    eq_buckets = fetch_equity_metrics(excluded_symbols=swing_held | intraday_held) if not eq_halted else {"AG1_LOW_RISK": [], "AG2_MID_RISK": [], "AG3_HIGH_RISK": []}
    fno_ctx = fetch_index_fno_context() if not fno_halted else {}

    trust_map = compute_intraday_agent_trust(records)
    try:
        msgs_all = sh.worksheet("agent_messages").get_all_records()
        recent_lessons = get_recent_intraday_lessons(msgs_all, limit=5)
    except Exception:
        recent_lessons = []

    client = genai.Client(api_key=api_key.strip())
    prompt = (
        "You are AG4_UNIFIED, Chief Multi-Asset Auditor for NSE India (Book A: Intraday Equity MIS; Book B: NIFTY/BANKNIFTY Index Options). "
        f"Agent Intraday Trust Scores (0-100): {json.dumps(trust_map)}. "
        f"Recent Intraday Self-Reflection Lessons: {json.dumps(recent_lessons)}. "
        f"Equity Candidates by Agent (already filtered for VWAP & EMA9>EMA20, excluding swing holdings): {json.dumps(eq_buckets)}. "
        f"Index Options & India VIX Context (includes directional_bias & ADX_14): {json.dumps(fno_ctx)}. "
        "Rules: "
        "1) Classify market_regime accurately. "
        "2) Select up to 2 high-probability Equity MIS buys (SL = 1x ATR, Target = 2x ATR) if eq_buckets non-empty. "
        "3) For Index Options, NEVER force CE buys blindly: choose CE only on confirmed bullish directional_bias, PE on confirmed bearish directional_bias, or SKIP during chop/indecision. Use SINGLE_LEG_BUY only when india_vix < 17 and ADX >= 22; use DEBIT_SPREAD when india_vix >= 17. Set SL = -20% of entry_premium, Target = +40% of entry_premium."
    )

    try:
        coro = client.aio.models.generate_content(
            model=CANDIDATE_MODELS[0],
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=UnifiedIntradayCommitteeOutput,
                temperature=0.15,
            ),
        )
        resp = await asyncio.wait_for(coro, timeout=14.0)
        verdict = UnifiedIntradayCommitteeOutput(**json.loads(resp.text))
    except Exception:
        # Deterministic Quant Fallback if Gemini times out
        eq_fb = []
        for ag_key, tier_name in [("AG2_MID_RISK", "MID"), ("AG1_LOW_RISK", "LOW")]:
            if eq_buckets.get(ag_key):
                top = eq_buckets[ag_key][0]
                eq_fb.append(IntradayEquityDecision(
                    symbol=top["symbol"],
                    source_agent=ag_key,
                    risk_tier=tier_name,
                    action="BUY",
                    conviction=74,
                    entry_price=top["ltp"],
                    stop_loss=round(top["ltp"] - top["atr"], 2),
                    target_price=round(top["ltp"] + 2.0 * top["atr"], 2),
                    reasoning=f"[{ag_key}] Intraday VWAP + EMA9>EMA20 momentum (RVOL {top['rvol']}x).",
                ))
                break

        fno_fb = []
        n_info = fno_ctx.get("indices", {}).get("NIFTY")
        for idx_k in ("NIFTY", "BANKNIFTY"):
            idx_inf = fno_ctx.get("indices", {}).get(idx_k)
            if not idx_inf:
                continue
            dir_bias = idx_inf.get("directional_bias", "SKIP_CHOP")
            if dir_bias in ("BULLISH_CE", "BEARISH_PE") and abs(idx_inf.get("intraday_chg_pct", 0)) >= 0.35:
                opt_t = "CE" if dir_bias == "BULLISH_CE" else "PE"
                atm = idx_inf["atm_strike"]
                row_atm = next((c for c in idx_inf["chain"] if c["strike"] == atm), None)
                if row_atm:
                    prem = row_atm["ce_ltp"] if opt_t == "CE" else row_atm["pe_ltp"]
                    if prem >= MIN_OPTION_PREMIUM:
                        strat_choice = "DEBIT_SPREAD" if float(fno_ctx.get("india_vix", 14.5)) >= 17.0 else "SINGLE_LEG_BUY"
                        step_sz = STRIKE_STEPS[idx_k] * 2
                        hedge_k = (atm + step_sz) if (strat_choice == "DEBIT_SPREAD" and opt_t == "CE") else ((atm - step_sz) if strat_choice == "DEBIT_SPREAD" else 0)
                        fno_fb.append(IntradayFnODecision(
                            index_name=idx_k,
                            source_agent="AG2_MID_RISK",
                            strategy=strat_choice,
                            option_type=opt_t,
                            strike=atm,
                            hedge_strike=hedge_k,
                            conviction=74,
                            entry_premium=prem,
                            stop_loss_premium=round(prem * 0.80, 2),
                            target_premium=round(prem * 1.40, 2),
                            reasoning=f"[AG2_MID_RISK] {idx_k} {dir_bias} ADX={idx_inf.get('adx_14')} RSI={idx_inf.get('rsi_14')} chg={idx_inf['intraday_chg_pct']:+.2f}% VIX={fno_ctx.get('india_vix')}.",
                        ))
                        break
        verdict = UnifiedIntradayCommitteeOutput(
            market_regime="TRENDING_BULL" if (n_info and n_info.get("intraday_chg_pct", 0) > 0) else "RANGE_CHOP",
            equity_decisions=eq_fb,
            fno_decisions=fno_fb,
        )

    eq_cash = float(acct_ws.acell("B2").value or INIT_EQ_CAPITAL)
    fno_cash = float(acct_ws.acell("C2").value or INIT_FNO_CAPITAL)
    cash_updated = False

    # Execute Book A: Equity MIS Orders (with strict R:R >= 1.60, initial_sl < entry < target, and sector cap)
    if not eq_halted:
        for ed in verdict.equity_decisions:
            if len(open_eq) >= MAX_EQ_POSITIONS:
                break
            if ed.action != "BUY" or ed.conviction < 65 or ed.symbol in intraday_held or ed.symbol in swing_held:
                continue
            rr_ok, rr_val, rr_msg = validate_entry_rr(ed.entry_price, ed.stop_loss, ed.target_price, min_rr=MIN_INTRADAY_RR)
            if not rr_ok:
                log_shadow_skip(sh, cycle_id, "EQUITY", ed.symbol, rr_msg, ed.entry_price)
                continue
            sec_ok, sec_msg = can_add_sector_position(ed.symbol, list(intraday_held | swing_held), max_per_sector=3)
            if not sec_ok:
                log_shadow_skip(sh, cycle_id, "EQUITY", ed.symbol, sec_msg, ed.entry_price)
                continue
            risk_per_sh = max(ed.entry_price - ed.stop_loss, ed.entry_price * 0.003)
            qty_risk = int(EQ_RISK_PER_TRADE // risk_per_sh)
            max_notional = min(eq_cash * EQ_MIS_LEVERAGE * 0.33, 166_666.0)
            qty_cap = int(max_notional // ed.entry_price)
            qty = min(qty_risk, qty_cap)
            margin_req = round((qty * ed.entry_price) / EQ_MIS_LEVERAGE, 2)
            if qty > 0 and margin_req <= eq_cash:
                eq_cash -= margin_req
                cash_updated = True
                intraday_held.add(ed.symbol)
                open_eq.append({"symbol": ed.symbol})
                trades_ws.append_row([
                    now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                    today_str, "EQUITY", ed.symbol, f"MIS_{ed.risk_tier}", "OPEN",
                    qty, 1, ed.entry_price, ed.stop_loss, ed.target_price, "", 0,
                    f"[{ed.source_agent}] {ed.reasoning}",
                    "BUY", ed.stop_loss, 0.0, 0.0
                ])
                log_intraday_msg(
                    sh, cycle_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "MIS_BUY",
                    f"⚡ [Book A MIS] BOUGHT {qty} {ed.symbol} @ ₹{ed.entry_price} | SL: ₹{ed.stop_loss} | TP: ₹{ed.target_price}",
                    ed.model_dump()
                )

    # Execute Book B: Index F&O Options Orders
    if not fno_halted:
        for fd in verdict.fno_decisions:
            if len(open_fno) >= MAX_FNO_POSITIONS:
                break
            if fd.strategy == "SKIP" or fd.conviction < 65:
                continue
            idx_obj = fno_ctx.get("indices", {}).get(fd.index_name, {})
            idx_bias = idx_obj.get("directional_bias", "")
            if idx_bias == "SKIP_CHOP":
                continue
            if idx_bias == "BULLISH_CE" and fd.option_type != "CE":
                continue
            if idx_bias == "BEARISH_PE" and fd.option_type != "PE":
                continue
            lot_sz = LOT_SIZES[fd.index_name]
            prem = float(fd.entry_premium)
            if prem < MIN_OPTION_PREMIUM:
                continue
            lot_cost = round(prem * lot_sz, 2)
            if lot_cost <= min(fno_cash, 50_000.0):
                fno_cash -= lot_cost
                cash_updated = True
                open_fno.append({"symbol": fd.index_name})
                contract_sym = (
                    f"{fd.index_name}_{fd.strike}_{fd.hedge_strike}_{fd.option_type}_SPREAD"
                    if fd.strategy == "DEBIT_SPREAD" and fd.hedge_strike > 0
                    else f"{fd.index_name}_{fd.strike}_{fd.option_type}"
                )
                sl_p = round(prem * 0.80, 2)
                tp_p = round(prem * 1.40, 2)
                trades_ws.append_row([
                    now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                    today_str, "FNO", contract_sym, fd.strategy, "OPEN",
                    1, lot_sz, prem, sl_p, tp_p, "", 0,
                    f"[{fd.source_agent}] {fd.reasoning}",
                    "BUY", sl_p, 0.0, 0.0
                ])
                log_intraday_msg(
                    sh, cycle_id, "AG5_EXECUTION_BROKER", "INTRADAY_LEDGER", "FNO_BUY",
                    f"🎯 [Book B F&O] BOUGHT 1 Lot {contract_sym} ({lot_sz} qty) @ ₹{prem} | SL: ₹{sl_p} | TP: ₹{tp_p}",
                    fd.model_dump()
                )

    if cash_updated:
        peak_p = float(acct_ws.acell("D2").value or 0.0)
        lock_f = float(acct_ws.acell("E2").value or 0.0)
        acct_ws.update("A2:E2", [[today_str, round(eq_cash, 2), round(fno_cash, 2), peak_p, lock_f]])

    log_intraday_msg(
        sh, cycle_id, "AG4_CHIEF_ANALYST", "AG5_EXECUTION_BROKER", "INTRADAY_VERDICT",
        f"Regime: {verdict.market_regime} | VIX: {fno_ctx.get('india_vix', 'N/A')} | MIS Props: {len(verdict.equity_decisions)} | F&O Props: {len(verdict.fno_decisions)}",
        verdict.model_dump()
    )




def close_specific_intraday_deals(gcp_creds_dict: dict, row_indices: list[int], reason: str = "MANUAL_CLOSE") -> tuple[bool, str]:
    """Closes specific 1-based sheet row indices (from trades worksheet) at current market/BS price and credits cash."""
    if not row_indices:
        return False, "No deals selected."
    sh = get_gsheet_client(gcp_creds_dict, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(sh)
    acct_ws = sh.worksheet("account")
    trades_ws = sh.worksheet("trades")
    records = trades_ws.get_all_records()

    target_set = {int(r) for r in row_indices}
    selected = [(idx, row) for idx, row in enumerate(records, start=2) if idx in target_set and row.get("status") == "OPEN"]
    if not selected:
        return False, "Selected deals are already closed or not found."

    eq_syms = list({row["symbol"] for _, row in selected if row.get("book") == "EQUITY"})
    has_fno = any(row.get("book") == "FNO" for _, row in selected)

    eq_ltps = {}
    if eq_syms:
        try:
            q_data = yf.download(eq_syms, period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
            for s in eq_syms:
                sub = q_data[s]["Close"].dropna() if len(eq_syms) > 1 else q_data["Close"].dropna()
                if not sub.empty:
                    eq_ltps[s] = round(float(sub.iloc[-1]), 2)
        except Exception:
            pass

    spot_map = {}
    sigma = 0.145
    if has_fno:
        try:
            idx_data = yf.download(["^NSEI", "^NSEBANK", "^INDIAVIX"], period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
            if "^INDIAVIX" in idx_data.columns.get_level_values(0):
                v_s = idx_data["^INDIAVIX"]["Close"].dropna()
                if not v_s.empty:
                    sigma = max(0.11, min(0.35, float(v_s.iloc[-1]) / 100.0))
            for k, y_sym in [("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")]:
                if y_sym in idx_data.columns.get_level_values(0):
                    s_df = idx_data[y_sym]["Close"].dropna()
                    if not s_df.empty:
                        spot_map[k] = float(s_df.iloc[-1])
        except Exception:
            pass

    eq_cash = float(acct_ws.acell("B2").value or INIT_EQ_CAPITAL)
    fno_cash = float(acct_ws.acell("C2").value or INIT_FNO_CAPITAL)
    peak_pnl = float(acct_ws.acell("D2").value or 0.0)
    locked_floor = float(acct_ws.acell("E2").value or 0.0)

    closed_count = 0
    total_net = 0.0
    cycle_id = now_ist().strftime("MAN-%H%M%S-IST")

    for idx, row in selected:
        book = str(row.get("book", "EQUITY"))
        sym = str(row["symbol"])
        qty_or_lots = int(float(row["qty_or_lots"]))
        lot_size = int(float(row.get("lot_size", 1) or 1))
        entry = float(row["entry"])

        if book == "EQUITY":
            exit_px = float(eq_ltps.get(sym, entry))
            units = qty_or_lots
            fee_buf = round((entry + exit_px) * units * EQ_ROUND_TRIP_COST_PCT, 2)
            gross_pnl = round((exit_px - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            eq_cash += ((entry * units) / EQ_MIS_LEVERAGE) + net_pnl
        else:
            parsed = parse_fno_symbol(sym)
            exit_px = entry
            if parsed and parsed["index_name"] in spot_map:
                sp_px = spot_map[parsed["index_name"]]
                t_yr = (days_to_next_tuesday() if parsed["index_name"] == "NIFTY" else 14.0) / 365.0
                p1 = black_scholes_price(sp_px, parsed["strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                if parsed["hedge_strike"] > 0:
                    p2 = black_scholes_price(sp_px, parsed["hedge_strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                    exit_px = round(max(p1 - p2, 1.0), 2)
                else:
                    exit_px = p1
            units = qty_or_lots * lot_size
            fee_buf = round(qty_or_lots * FNO_ROUND_TRIP_FEE_PER_LOT, 2)
            gross_pnl = round((exit_px - entry) * units, 2)
            net_pnl = round(gross_pnl - fee_buf, 2)
            fno_cash += (entry * units) + net_pnl

        trades_ws.update(f"F{idx}", [[reason]])
        trades_ws.update(f"L{idx}:M{idx}", [[exit_px, net_pnl]])
        trades_ws.update(f"Q{idx}:R{idx}", [[gross_pnl, fee_buf]])
        log_intraday_msg(
            sh, cycle_id, "OPERATOR_MANUAL", "INTRADAY_LEDGER", reason,
            f"✋ Manual Close [{book}] {sym} @ ₹{exit_px} | Net P&L: ₹{net_pnl:+,.2f}",
            {"book": book, "symbol": sym, "exit_price": exit_px, "net_pnl": net_pnl}
        )
        closed_count += 1
        total_net = round(total_net + net_pnl, 2)

    if closed_count > 0:
        remaining_open_count = sum(1 for i_r, r_o in enumerate(records, start=2) if r_o.get("status") == "OPEN" and i_r not in target_set)
        if remaining_open_count == 0:
            peak_pnl = 0.0
            locked_floor = 0.0
        today_str = now_ist().strftime("%Y-%m-%d")
        acct_ws.update("A2:E2", [[today_str, round(eq_cash, 2), round(fno_cash, 2), round(peak_pnl, 2), round(locked_floor, 2)]])
        return True, f"Closed {closed_count} deal(s) ({reason}) | Net P&L: ₹{total_net:+,.2f}"
    return False, "No open deals matched."


async def run_fast_watchdog_loop(api_key: str, gcp_creds_dict: dict, duration_sec: int = 260, poll_interval_sec: int = 20):
    await run_unified_cycle(api_key, gcp_creds_dict, force_square_off=False)
    sh = get_gsheet_client(gcp_creds_dict, INTRADAY_SHEET_NAME)
    ticks = max(1, duration_sec // poll_interval_sec)
    for t in range(1, ticks + 1):
        if not is_nse_open_now():
            break
        time.sleep(poll_interval_sec)
        try:
            dt = now_ist()
            force_eod = (dt.hour * 60 + dt.minute) >= get_eod_squareoff_minutes(CAS_DEC7_2026_ENABLED)
            evaluate_exits_and_trailing_sl(sh, dt.strftime(f"IWD-{t:02d}-%H%M%S"), force_square_off=force_eod)
            if force_eod:
                break
        except Exception:
            continue
