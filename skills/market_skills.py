import numpy as np
import pandas as pd

SECTOR_MAP = {
    "HDFCBANK.NS": "BANKING", "ICICIBANK.NS": "BANKING", "SBIN.NS": "BANKING",
    "AXISBANK.NS": "BANKING", "KOTAKBANK.NS": "BANKING", "BANKBARODA.NS": "BANKING",
    "PNB.NS": "BANKING", "CANBK.NS": "BANKING", "INDUSINDBK.NS": "BANKING",
    "BAJFINANCE.NS": "NBFC_FIN", "BAJAJFINSV.NS": "NBFC_FIN", "CHOLAFIN.NS": "NBFC_FIN",
    "SHRIRAMFIN.NS": "NBFC_FIN", "RECLTD.NS": "NBFC_FIN", "PFC.NS": "NBFC_FIN",
    "INFY.NS": "IT", "TCS.NS": "IT", "TECHM.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
    "MARUTI.NS": "AUTO", "M&M.NS": "AUTO", "TATAMOTORS.NS": "AUTO", "BAJAJ-AUTO.NS": "AUTO",
    "EICHERMOT.NS": "AUTO", "TVSMOTOR.NS": "AUTO",
    "SUNPHARMA.NS": "PHARMA", "CIPLA.NS": "PHARMA", "DRREDDY.NS": "PHARMA",
    "DIVISLAB.NS": "PHARMA", "APOLLOHOSP.NS": "PHARMA",
    "RELIANCE.NS": "ENERGY_O2C", "ONGC.NS": "ENERGY_O2C", "COALINDIA.NS": "ENERGY_O2C",
    "BPCL.NS": "ENERGY_O2C", "NTPC.NS": "POWER_UTILITY", "POWERGRID.NS": "POWER_UTILITY",
    "TATAPOWER.NS": "POWER_UTILITY",
    "TATASTEEL.NS": "METALS", "JSWSTEEL.NS": "METALS", "HINDALCO.NS": "METALS", "VEDL.NS": "METALS",
    "HAL.NS": "DEFENCE_CAPGOODS", "BEL.NS": "DEFENCE_CAPGOODS", "LT.NS": "DEFENCE_CAPGOODS",
    "SIEMENS.NS": "DEFENCE_CAPGOODS", "ABB.NS": "DEFENCE_CAPGOODS", "BHEL.NS": "DEFENCE_CAPGOODS",
    "CUMMINSIND.NS": "DEFENCE_CAPGOODS", "POLYCAB.NS": "DEFENCE_CAPGOODS",
    "TRENT.NS": "CONSUMER_RETAIL", "TITAN.NS": "CONSUMER_RETAIL", "INDIGO.NS": "CONSUMER_RETAIL",
    "IRCTC.NS": "CONSUMER_RETAIL", "ITC.NS": "FMCG", "HINDUNILVR.NS": "FMCG",
    "ASIANPAINT.NS": "FMCG", "DLF.NS": "REALTY", "ULTRACEMCO.NS": "CEMENT",
    "ADANIENT.NS": "INFRA_CONGL", "ADANIPORTS.NS": "INFRA_CONGL",
}


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(str(symbol).upper(), "OTHER")


def compute_adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    if len(close) < period + 2:
        return pd.Series(dtype=float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=close.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=close.index)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).replace(0, np.nan)

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def evaluate_intraday_chop_skill(df_5m: pd.DataFrame, min_adx: float = 22.0) -> dict:
    """Evaluates 5m ADX(14), VWAP slope, and Opening Range (first 3 bars = 9:15-9:30 AM IST)."""
    if df_5m is None or len(df_5m) < 20:
        return {
            "adx": 0.0,
            "is_trending": False,
            "vwap_slope_pct": 0.0,
            "orb_high": None,
            "orb_low": None,
            "orb_signal": "NEUTRAL",
            "directional_bias": "SKIP_CHOP",
        }

    adx_s = compute_adx_series(df_5m["High"], df_5m["Low"], df_5m["Close"], period=14).dropna()
    adx_val = round(float(adx_s.iloc[-1]), 2) if not adx_s.empty else 0.0

    day_df = df_5m.iloc[-min(78, len(df_5m)):].copy()
    typ = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3.0
    cum_vol = day_df["Volume"].cumsum().replace(0, np.nan)
    vwap_s = ((typ * day_df["Volume"]).cumsum() / cum_vol).ffill().bfill()
    vwap_now = float(vwap_s.iloc[-1]) if not vwap_s.empty else float(day_df["Close"].iloc[-1])
    vwap_prev = float(vwap_s.iloc[-min(4, len(vwap_s))]) if not vwap_s.empty else vwap_now
    vwap_slope_pct = round(((vwap_now - vwap_prev) / max(vwap_prev, 1.0)) * 100.0, 4)

    orb_bars = day_df.iloc[:min(3, len(day_df))]
    orb_high = round(float(orb_bars["High"].max()), 2)
    orb_low = round(float(orb_bars["Low"].min()), 2)
    ltp = float(day_df["Close"].iloc[-1])

    if ltp > orb_high and ltp >= vwap_now:
        orb_signal = "BULL_BREAKOUT"
    elif ltp < orb_low and ltp <= vwap_now:
        orb_signal = "BEAR_BREAKDOWN"
    else:
        orb_signal = "INSIDE_OR_MIXED"

    is_trending = adx_val >= min_adx
    if not is_trending:
        bias = "SKIP_CHOP"
    elif ltp > vwap_now and vwap_slope_pct >= 0.0:
        bias = "BULLISH_CE"
    elif ltp < vwap_now and vwap_slope_pct <= 0.0:
        bias = "BEARISH_PE"
    else:
        bias = "SKIP_CHOP"

    return {
        "adx": adx_val,
        "is_trending": is_trending,
        "vwap": round(vwap_now, 2),
        "vwap_slope_pct": vwap_slope_pct,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_signal": orb_signal,
        "directional_bias": bias,
    }


def evaluate_swing_multi_tf_skill(df_1d: pd.DataFrame, benchmark_close: pd.Series | None = None) -> dict:
    """Computes Weekly 20-EMA alignment and 20-day Relative Strength vs benchmark."""
    if df_1d is None or len(df_1d) < 40:
        return {"weekly_trend_ok": True, "rs_20d_pct": 0.0, "rel_strength_vs_nifty_pct": 0.0}

    close_1d = df_1d["Close"].dropna()
    weekly_close = close_1d.resample("W-FRI").last().dropna()
    if len(weekly_close) >= 20:
        w_ema20 = float(weekly_close.ewm(span=20).mean().iloc[-1])
        weekly_ok = bool(float(close_1d.iloc[-1]) >= w_ema20)
    else:
        weekly_ok = True

    ret_20d = ((float(close_1d.iloc[-1]) / max(float(close_1d.iloc[-20]), 1e-6)) - 1.0) * 100.0 if len(close_1d) >= 20 else 0.0
    bench_20d = 0.0
    if benchmark_close is not None and len(benchmark_close.dropna()) >= 20:
        b_s = benchmark_close.dropna()
        bench_20d = ((float(b_s.iloc[-1]) / max(float(b_s.iloc[-20]), 1e-6)) - 1.0) * 100.0

    rel_vs_nifty = round(ret_20d - bench_20d, 2)
    return {
        "weekly_trend_ok": weekly_ok,
        "ret_20d_pct": round(ret_20d, 2),
        "rel_strength_vs_nifty_pct": rel_vs_nifty,
    }


def get_sector_counts(symbols: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in symbols or []:
        sec = get_sector(s)
        counts[sec] = counts.get(sec, 0) + 1
    return counts


def can_add_sector_position(symbol: str, open_symbols: list[str], max_per_sector: int = 3) -> tuple[bool, str]:
    sec = get_sector(symbol)
    counts = get_sector_counts(open_symbols)
    cur = counts.get(sec, 0)
    if cur >= max_per_sector:
        return False, f"Sector cap reached for {sec} ({cur}/{max_per_sector})"
    return True, f"Sector {sec} OK ({cur}/{max_per_sector})"
