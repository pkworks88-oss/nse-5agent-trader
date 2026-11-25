from typing import Any, Dict, List, Optional
import math
import numpy as np
import pandas as pd

from core.config import REGIME_CHG_PCT, REGIME_VIX_MAX


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 55.0
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    val = float((100.0 - (100.0 / (1.0 + rs))).iloc[-1])
    if float(avg_loss.iloc[-1]) == 0.0 and float(avg_gain.iloc[-1]) > 0.0:
        return 100.0                       # all-gain streak is RSI 100, not 'unknown 55'
    return 55.0 if (math.isnan(val) or math.isinf(val)) else round(val, 2)


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < 2:
        last_c = float(df["Close"].iloc[-1]) if len(df) else 100.0
        return round(last_c * 0.015, 2)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    prev_close = df["Close"].astype(float).shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return round(max(0.05, float(tr.ewm(alpha=1.0 / period, min_periods=1, adjust=False).mean().iloc[-1])), 2)


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 2:
        return 22.0
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().replace(0.0, np.nan)
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    val = float(dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().iloc[-1])
    return 22.0 if (math.isnan(val) or math.isinf(val)) else round(val, 2)


def compute_vwap(df: pd.DataFrame) -> float:
    if len(df) == 0:
        return 0.0
    tp = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3.0
    vol = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(1.0, index=df.index)
    cum_vol = float(vol.sum())
    return round(float(tp.iloc[-1]), 2) if cum_vol <= 0 else round(float((tp * vol).sum() / cum_vol), 2)


def compute_rvol(df: pd.DataFrame, lookback: int = 20) -> float:
    if "Volume" not in df.columns or len(df) < 2:
        return 1.25
    vol = df["Volume"].astype(float)
    cur_v = float(vol.iloc[-1])
    avg_v = float(vol.iloc[max(0, len(vol) - lookback - 1):-1].mean())
    return 1.20 if (avg_v <= 0 or cur_v <= 0) else round(cur_v / avg_v, 2)


def analyze_ohlcv_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    close = df["Close"].astype(float)
    ltp = round(float(close.iloc[-1]), 2)
    prev_close = round(float(close.iloc[-2]), 2) if len(close) >= 2 else ltp
    pdh = round(float(df["High"].iloc[-2]), 2) if len(df) >= 2 else round(float(df["High"].iloc[-1]), 2)
    pdl = round(float(df["Low"].iloc[-2]), 2) if len(df) >= 2 else round(float(df["Low"].iloc[-1]), 2)
    bar_high = round(float(df["High"].iloc[-1]), 2)
    bar_low = round(float(df["Low"].iloc[-1]), 2)
    ema9 = round(float(compute_ema(close, 9).iloc[-1]), 2)
    ema20 = round(float(compute_ema(close, 20).iloc[-1]), 2)
    ema50 = round(float(compute_ema(close, 50).iloc[-1]), 2)
    ema200 = round(float(compute_ema(close, 200).iloc[-1]), 2)
    rsi = compute_rsi(close, 14)
    atr = compute_atr(df, 14)
    adx = compute_adx(df, 14)
    vwap = compute_vwap(df.tail(24))
    rvol = compute_rvol(df, 20)
    chg_pct = round(((ltp - prev_close) / max(prev_close, 0.01)) * 100.0, 2)
    return {
        "ltp": ltp, "prev_close": prev_close, "chg_pct": chg_pct,
        "pdh": pdh, "pdl": pdl, "bar_high": bar_high, "bar_low": bar_low,
        "ema9": ema9, "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "rsi": rsi, "atr": atr, "adx": adx, "vwap": vwap, "rvol": rvol,
    }


def score_setup_from_indicators(ind: Dict[str, Any], desk: str = "SWING") -> Dict[str, Any]:
    ltp = float(ind["ltp"])
    atr = max(0.05, float(ind["atr"]))
    rsi = float(ind["rsi"])
    adx = float(ind["adx"])
    rvol = float(ind["rvol"])
    ema9 = float(ind.get("ema9", round(ltp * 0.996, 2)))
    ema20 = float(ind.get("ema20", round(ltp * 0.990, 2)))
    ema50 = float(ind.get("ema50", round(ltp * 0.975, 2)))
    vwap = float(ind["vwap"])
    d_up = desk.strip().upper()

    score = 52
    reasons: List[str] = []
    if ltp > ema20:
        score += 8
        reasons.append("LTP > EMA20")
    if ema9 > ema20:
        score += 7
        reasons.append("EMA9 > EMA20")
    if ltp > ema50:
        score += 5
    if ltp >= vwap:
        score += 5
        reasons.append("Above VWAP")
    if 48.0 <= rsi <= 68.0:
        score += 8
        reasons.append(f"RSI {rsi:.1f} optimal")
    elif rsi > 72.0:
        score -= 12
        reasons.append(f"RSI {rsi:.1f} overbought")
    if adx >= 20.0:
        score += 6
        reasons.append(f"ADX {adx:.1f} trending")
    if rvol >= 1.3:
        score += 6
        reasons.append(f"RVOL {rvol:.2f}x")

    conviction = max(40, min(94, score))
    if d_up == "INTRADAY":
        sl_mult, tgt_mult = 0.85, 1.85
    elif d_up in ("FNO", "F&O"):
        sl_mult, tgt_mult = 1.00, 2.20
    elif d_up == "COMMODITY":
        sl_mult, tgt_mult = 1.00, 2.00
    else:
        sl_mult, tgt_mult = 1.25, 2.60

    entry = round(ltp, 2)
    sl = round(max(0.05, entry - sl_mult * atr), 2)
    target = round(entry + tgt_mult * atr, 2)
    return {
        "entry": entry, "sl": sl, "target": target, "conviction": conviction,
        "rationale": f"{', '.join(reasons) or 'Technical trend scan'} (EMA9={ema9}, EMA20={ema20}, ATR={atr}, ADX={adx}).",
    }


SECTOR_MAP: Dict[str, str] = {
    "RELIANCE.NS": "ENERGY", "ONGC.NS": "ENERGY", "BPCL.NS": "ENERGY", "IOC.NS": "ENERGY", "COALINDIA.NS": "MINING",
    "TCS.NS": "IT", "INFY.NS": "IT", "TECHM.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
    "HDFCBANK.NS": "BANKING", "ICICIBANK.NS": "BANKING", "SBIN.NS": "BANKING", "AXISBANK.NS": "BANKING",
    "KOTAKBANK.NS": "BANKING", "INDUSINDBK.NS": "BANKING", "FEDERALBNK.NS": "BANKING", "BANKBARODA.NS": "BANKING",
    "BAJFINANCE.NS": "FINANCE", "PFC.NS": "FINANCE", "MUTHOOTFIN.NS": "FINANCE", "ICICIGI.NS": "INSURANCE", "LICI.NS": "INSURANCE",
    "SUNPHARMA.NS": "PHARMA", "CIPLA.NS": "PHARMA", "DRREDDY.NS": "PHARMA", "TORNTPHARM.NS": "PHARMA", "LUPIN.NS": "PHARMA",
    "TVSMOTOR.NS": "AUTO", "M&M.NS": "AUTO", "MARUTI.NS": "AUTO", "EICHERMOT.NS": "AUTO",
    "TATASTEEL.NS": "METALS", "JSWSTEEL.NS": "METALS", "HINDALCO.NS": "METALS",
    "LT.NS": "INFRA", "ADANIENT.NS": "CONGLOMERATE", "DIXON.NS": "ELECTRONICS", "SRF.NS": "CHEMICALS",
    "GRASIM.NS": "CEMENT", "DABUR.NS": "FMCG", "TATAPOWER.NS": "POWER", "RVNL.NS": "RAILWAYS", "BHARTIARTL.NS": "TELECOM",
}


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(str(symbol).strip().upper(), "GENERAL")


def compute_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(55.0)


def compute_adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    up = h.diff()
    dn = -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().replace(0.0, np.nan)
    pdi = 100.0 * pdm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    ndi = 100.0 * ndm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    dx = 100.0 * (pdi - ndi).abs() / (pdi + ndi).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().fillna(22.0)


def compute_pdh_pdl(df: pd.DataFrame) -> tuple[float, float]:
    if df is None or len(df) < 2:
        val = float(df["Close"].iloc[-1]) if (df is not None and len(df) > 0) else 100.0
        return round(val * 1.01, 2), round(val * 0.99, 2)
    return round(float(df["High"].iloc[-2]), 2), round(float(df["Low"].iloc[-2]), 2)


def get_sector_counts(open_trades: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for t in open_trades:
        sec = str(t.get("sector") or get_sector(t.get("symbol", ""))).strip().upper() or "GENERAL"
        counts[sec] = counts.get(sec, 0) + 1
    return counts


def can_add_sector_position(symbol: str, open_trades: List[Dict[str, Any]], max_per_sector: int = 3) -> tuple[bool, str]:
    sec = get_sector(symbol)
    cur = get_sector_counts(open_trades).get(sec, 0)
    if cur >= max_per_sector:
        return False, f"Sector {sec} cap reached ({cur}/{max_per_sector})"
    return True, f"Sector {sec} OK ({cur}/{max_per_sector})"


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_price(spot: float, strike: float, t_years: float, iv: float = 0.16, option_type: str = "CE", r: float = 0.065) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return round(max(1.0, (spot - strike) if option_type.upper() == "CE" else (strike - spot)), 2)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    if option_type.upper() == "CE":
        px = spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        px = strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return round(max(1.0, px), 2)


def black_scholes_delta(spot: float, strike: float, t_years: float, iv: float = 0.16, option_type: str = "CE", r: float = 0.065) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.5
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    return round(_norm_cdf(d1) if option_type.upper() == "CE" else (_norm_cdf(d1) - 1.0), 3)


def days_to_next_tuesday(from_date: Optional[Any] = None) -> int:
    from datetime import date, datetime
    if from_date is None:
        d = datetime.now().date()
    elif isinstance(from_date, datetime):
        d = from_date.date()
    else:
        d = from_date
    # Tuesday is weekday 1
    days_ahead = (1 - d.weekday()) % 7
    return 7 if days_ahead == 0 else days_ahead


def parse_fno_symbol(sym: str) -> Dict[str, Any]:
    parts = str(sym).strip().upper().replace("_", " ").split()
    if len(parts) >= 3 and parts[0] in ("NIFTY", "BANKNIFTY") and parts[-1] in ("CE", "PE"):
        try:
            strike = int(float(parts[-2]))
            return {"valid": True, "index": parts[0], "strike": strike, "option_type": parts[-1]}
        except ValueError:
            pass
    return {"valid": False, "index": "", "strike": 0, "option_type": ""}


def validate_swing_entry_brackets(entry: float, initial_sl: float, target: float, min_rr: float = 1.60, rsi_14: Optional[float] = None, max_rsi: float = 72.0) -> tuple[bool, float, str]:
    if not (initial_sl < entry < target):
        return False, 0.0, f"Invalid swing brackets: initial_sl ({initial_sl}) < entry ({entry}) < target ({target})"
    if rsi_14 is not None and float(rsi_14) > max_rsi:
        return False, 0.0, f"Swing RSI(14) {rsi_14:.1f} > {max_rsi:.1f}"
    rr = round((target - entry) / max(entry - initial_sl, 1e-6), 2)
    if rr < min_rr:
        return False, rr, f"Swing R:R 1:{rr:.2f} < 1:{min_rr:.2f}"
    return True, rr, f"Swing R:R 1:{rr:.2f} OK"


def compute_swing_open_downside_risk(open_trades: List[Dict[str, Any]]) -> float:
    total_risk = 0.0
    for t in open_trades:
        if str(t.get("status", "")).upper() != "OPEN":
            continue
        entry = float(t.get("entry", 0.0) or 0.0)
        sl = float(t.get("sl", 0.0) or 0.0)
        qty = int(float(t.get("qty", 0) or 0))
        if sl < entry and qty > 0:
            total_risk += (entry - sl) * qty
    return round(total_risk, 2)


def classify_swing_regime(close: float, sma20: Optional[float], chg_pct: float, vix: Optional[float] = None) -> Dict[str, Any]:
    """Pure Nifty regime rule (shared by live runner and backtest). DEFENSIVE if any trigger fires."""
    reasons: List[str] = []
    if sma20 is not None and close < sma20:
        reasons.append(f"Nifty {close:,.0f} below 20-SMA {sma20:,.0f}")
    if chg_pct <= REGIME_CHG_PCT:
        reasons.append(f"Nifty day change {chg_pct:+.2f}% <= {REGIME_CHG_PCT:.2f}%")
    if vix is not None and vix >= REGIME_VIX_MAX:
        reasons.append(f"India VIX {vix:.1f} >= {REGIME_VIX_MAX:.0f}")
    return {"mode": "DEFENSIVE" if reasons else "NORMAL", "defensive": bool(reasons), "reasons": reasons,
            "close": close, "sma20": sma20, "chg_pct": chg_pct, "vix": vix}
