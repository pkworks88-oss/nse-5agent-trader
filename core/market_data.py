from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import calendar
import math
import time
from datetime import date
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

from core.config import FALLBACK_VIX, FNO_IV_SKEW, GOLD_IMPORT_DUTY_FACTOR, ENTRY_CUTOFF_IST_HOUR, ENTRY_CUTOFF_IST_MINUTE, EOD_SQUAREOFF_IST_HOUR, EOD_SQUAREOFF_IST_MINUTE
from core.quant_engine import analyze_ohlcv_indicators, black_scholes_delta, black_scholes_price, classify_swing_regime, compute_vwap

NSE_HOLIDAYS_YYYY_MM_DD = {
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
}

FALLBACK_QUOTES: Dict[str, Dict[str, float]] = {
    "TATASTEEL.NS": {"ltp": 163.91, "chg_pct": -1.45, "pdh": 167.80, "pdl": 163.10, "bar_high": 164.50, "bar_low": 163.50, "rsi": 41.2, "rvol": 1.38, "atr": 3.6, "adx": 24.5, "vwap": 164.80},
    "ADANIENT.NS": {"ltp": 2712.60, "chg_pct": 0.82, "pdh": 2725.00, "pdl": 2672.00, "bar_high": 2718.00, "bar_low": 2705.00, "rsi": 63.5, "rvol": 1.65, "atr": 52.0, "adx": 26.2, "vwap": 2704.00},
    "DIXON.NS": {"ltp": 13368.00, "chg_pct": 2.15, "pdh": 13410.00, "pdl": 13080.00, "bar_high": 13390.00, "bar_low": 13320.00, "rsi": 66.8, "rvol": 1.92, "atr": 245.0, "adx": 29.4, "vwap": 13290.00},
    "TVSMOTOR.NS": {"ltp": 4564.30, "chg_pct": 1.42, "pdh": 4585.00, "pdl": 4490.00, "bar_high": 4572.00, "bar_low": 4550.00, "rsi": 62.4, "rvol": 1.54, "atr": 78.0, "adx": 25.8, "vwap": 4548.00},
    "CIPLA.NS": {"ltp": 1406.60, "chg_pct": -0.75, "pdh": 1422.00, "pdl": 1402.00, "bar_high": 1411.00, "bar_low": 1404.00, "rsi": 46.5, "rvol": 1.15, "atr": 21.5, "adx": 19.2, "vwap": 1412.00},
    "FEDERALBNK.NS": {"ltp": 365.90, "chg_pct": 0.65, "pdh": 368.00, "pdl": 361.50, "bar_high": 366.80, "bar_low": 364.90, "rsi": 60.8, "rvol": 1.48, "atr": 6.4, "adx": 23.1, "vwap": 364.80},
    "TORNTPHARM.NS": {"ltp": 4761.00, "chg_pct": 1.10, "pdh": 4778.00, "pdl": 4705.00, "bar_high": 4768.00, "bar_low": 4748.00, "rsi": 64.1, "rvol": 1.32, "atr": 68.0, "adx": 24.0, "vwap": 4745.00},
    "PFC.NS": {"ltp": 327.65, "chg_pct": 1.25, "pdh": 330.00, "pdl": 322.50, "bar_high": 328.40, "bar_low": 326.80, "rsi": 63.0, "rvol": 1.62, "atr": 6.8, "adx": 25.5, "vwap": 326.20},
    "ONGC.NS": {"ltp": 223.27, "chg_pct": -0.45, "pdh": 225.40, "pdl": 222.50, "bar_high": 223.90, "bar_low": 222.80, "rsi": 49.0, "rvol": 1.18, "atr": 4.1, "adx": 18.5, "vwap": 223.80},
    "TATAPOWER.NS": {"ltp": 335.20, "chg_pct": -0.60, "pdh": 338.50, "pdl": 334.00, "bar_high": 336.10, "bar_low": 334.60, "rsi": 48.2, "rvol": 1.22, "atr": 6.2, "adx": 20.1, "vwap": 336.10},
    "SRF.NS": {"ltp": 2507.10, "chg_pct": 1.35, "pdh": 2518.00, "pdl": 2472.00, "bar_high": 2512.00, "bar_low": 2498.00, "rsi": 64.2, "rvol": 1.51, "atr": 41.0, "adx": 26.4, "vwap": 2496.00},
    "LICI.NS": {"ltp": 409.65, "chg_pct": 1.40, "pdh": 412.00, "pdl": 403.50, "bar_high": 410.50, "bar_low": 408.40, "rsi": 61.5, "rvol": 1.46, "atr": 7.5, "adx": 22.8, "vwap": 408.10},
    "JSWSTEEL.NS": {"ltp": 1310.80, "chg_pct": -0.35, "pdh": 1322.00, "pdl": 1308.00, "bar_high": 1314.00, "bar_low": 1309.50, "rsi": 53.0, "rvol": 1.28, "atr": 23.0, "adx": 21.4, "vwap": 1313.50},
    "LUPIN.NS": {"ltp": 1990.10, "chg_pct": 0.25, "pdh": 2002.00, "pdl": 1978.00, "bar_high": 1994.00, "bar_low": 1984.00, "rsi": 55.2, "rvol": 1.24, "atr": 32.0, "adx": 20.8, "vwap": 1988.00},
    "GRASIM.NS": {"ltp": 3068.40, "chg_pct": 0.30, "pdh": 3085.00, "pdl": 3052.00, "bar_high": 3074.00, "bar_low": 3060.00, "rsi": 57.8, "rvol": 1.30, "atr": 48.0, "adx": 22.0, "vwap": 3064.00},
    "ICICIGI.NS": {"ltp": 1743.50, "chg_pct": 1.85, "pdh": 1750.00, "pdl": 1710.00, "bar_high": 1747.00, "bar_low": 1736.00, "rsi": 65.4, "rvol": 1.68, "atr": 29.0, "adx": 27.1, "vwap": 1734.00},
    "DRREDDY.NS": {"ltp": 1382.40, "chg_pct": -0.80, "pdh": 1398.00, "pdl": 1380.00, "bar_high": 1386.00, "bar_low": 1380.50, "rsi": 47.4, "rvol": 1.14, "atr": 21.0, "adx": 19.5, "vwap": 1387.00},
    "DABUR.NS": {"ltp": 395.75, "chg_pct": 1.15, "pdh": 398.00, "pdl": 390.50, "bar_high": 396.80, "bar_low": 394.50, "rsi": 62.1, "rvol": 1.44, "atr": 6.4, "adx": 23.6, "vwap": 394.20},
    "INDUSINDBK.NS": {"ltp": 980.85, "chg_pct": 1.20, "pdh": 992.00, "pdl": 968.00, "bar_high": 985.00, "bar_low": 979.50, "rsi": 61.0, "rvol": 1.52, "atr": 18.5, "adx": 24.8, "vwap": 982.00},
    "COALINDIA.NS": {"ltp": 388.80, "chg_pct": -0.55, "pdh": 393.50, "pdl": 387.80, "bar_high": 390.10, "bar_low": 388.10, "rsi": 51.2, "rvol": 1.21, "atr": 6.8, "adx": 20.4, "vwap": 390.20},
    "RELIANCE.NS": {"ltp": 1368.40, "chg_pct": 0.45, "pdh": 1374.00, "pdl": 1356.00, "bar_high": 1370.50, "bar_low": 1365.20, "rsi": 61.8, "rvol": 1.58, "atr": 21.5, "adx": 23.8, "vwap": 1364.80},
    "HDFCBANK.NS": {"ltp": 834.20, "chg_pct": 0.72, "pdh": 837.50, "pdl": 824.00, "bar_high": 835.80, "bar_low": 831.90, "rsi": 62.4, "rvol": 1.52, "atr": 11.4, "adx": 23.5, "vwap": 831.50},
    "TCS.NS": {"ltp": 2394.50, "chg_pct": 0.68, "pdh": 2405.00, "pdl": 2372.00, "bar_high": 2398.00, "bar_low": 2386.00, "rsi": 61.2, "rvol": 1.45, "atr": 34.0, "adx": 22.8, "vwap": 2388.00},
    "INFY.NS": {"ltp": 1866.20, "chg_pct": 0.58, "pdh": 1874.00, "pdl": 1848.00, "bar_high": 1869.00, "bar_low": 1860.00, "rsi": 60.5, "rvol": 1.42, "atr": 27.5, "adx": 22.1, "vwap": 1861.00},
    "ICICIBANK.NS": {"ltp": 1289.40, "chg_pct": 0.64, "pdh": 1295.00, "pdl": 1275.00, "bar_high": 1292.00, "bar_low": 1285.00, "rsi": 62.0, "rvol": 1.49, "atr": 16.8, "adx": 23.4, "vwap": 1286.00},
    "SBIN.NS": {"ltp": 848.10, "chg_pct": 0.78, "pdh": 851.50, "pdl": 837.00, "bar_high": 849.80, "bar_low": 845.20, "rsi": 63.1, "rvol": 1.56, "atr": 13.2, "adx": 24.2, "vwap": 845.50},
    "TECHM.NS": {"ltp": 1684.50, "chg_pct": 0.85, "pdh": 1690.00, "pdl": 1664.00, "bar_high": 1687.00, "bar_low": 1678.00, "rsi": 62.8, "rvol": 1.54, "atr": 22.0, "adx": 24.5, "vwap": 1679.00},
    "WIPRO.NS": {"ltp": 566.40, "chg_pct": 0.62, "pdh": 569.00, "pdl": 559.50, "bar_high": 567.80, "bar_low": 564.20, "rsi": 60.4, "rvol": 1.41, "atr": 8.4, "adx": 21.9, "vwap": 564.50},
    "SUNPHARMA.NS": {"ltp": 1792.00, "chg_pct": 0.74, "pdh": 1801.00, "pdl": 1774.00, "bar_high": 1796.00, "bar_low": 1785.00, "rsi": 61.6, "rvol": 1.44, "atr": 24.5, "adx": 23.0, "vwap": 1786.00},
    "LT.NS": {"ltp": 3585.00, "chg_pct": 0.80, "pdh": 3602.00, "pdl": 3548.00, "bar_high": 3592.00, "bar_low": 3570.00, "rsi": 62.5, "rvol": 1.50, "atr": 49.0, "adx": 24.1, "vwap": 3574.00},
    "M&M.NS": {"ltp": 2945.00, "chg_pct": 0.92, "pdh": 2960.00, "pdl": 2910.00, "bar_high": 2952.00, "bar_low": 2932.00, "rsi": 64.0, "rvol": 1.60, "atr": 42.0, "adx": 25.4, "vwap": 2935.00},
    "BAJFINANCE.NS": {"ltp": 7120.00, "chg_pct": 0.70, "pdh": 7155.00, "pdl": 7045.00, "bar_high": 7138.00, "bar_low": 7095.00, "rsi": 61.9, "rvol": 1.47, "atr": 98.0, "adx": 23.2, "vwap": 7102.00},
    "GOLDPETAL MCX": {"ltp": 7842.00, "chg_pct": 0.85, "pdh": 7855.00, "pdl": 7811.00, "bar_high": 7848.00, "bar_low": 7831.00, "rsi": 61.5, "rvol": 1.60, "atr": 24.0, "adx": 24.0, "vwap": 7834.00},
    "GOLDM MCX": {"ltp": 78420.00, "chg_pct": 0.85, "pdh": 78550.00, "pdl": 78110.00, "bar_high": 78480.00, "bar_low": 78310.00, "rsi": 61.5, "rvol": 1.60, "atr": 240.0, "adx": 24.0, "vwap": 78340.00},
    "CRUDEOILM MCX": {"ltp": 5865.00, "chg_pct": 0.43, "pdh": 5890.00, "pdl": 5810.00, "bar_high": 5878.00, "bar_low": 5845.00, "rsi": 59.0, "rvol": 1.45, "atr": 68.0, "adx": 22.1, "vwap": 5852.00},
}

# Feeds that are NOT real market data. The runner refuses to open trades on these
# (set ALLOW_FALLBACK_FEED=true only for offline demos).
STALE_FEEDS = {"fallback_calibrated", "entry_anchor_fallback", "index_spot_option_model_stale"}

_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}
_HISTORY_CACHE: Dict[str, Dict[str, Any]] = {}
QUOTE_CACHE_TTL_SECONDS: float = 18.0
_YF_RATE_LIMITED_UNTIL: float = 0.0
_YF_COOLDOWN_SECONDS: float = 120.0


def _yf_allowed() -> bool:
    return yf is not None and time.monotonic() >= _YF_RATE_LIMITED_UNTIL


def _mark_yf_rate_limited(exc: Exception) -> None:
    global _YF_RATE_LIMITED_UNTIL
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg or "crumb" in msg:
        _YF_RATE_LIMITED_UNTIL = time.monotonic() + _YF_COOLDOWN_SECONDS


def get_nse_session_status(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    raw_dt = now_utc or datetime.now(timezone.utc)
    dt_utc = raw_dt.astimezone(timezone.utc) if raw_dt.tzinfo else raw_dt.replace(tzinfo=timezone.utc)
    dt_ist = dt_utc + timedelta(hours=5, minutes=30)
    dt_iraq = dt_utc + timedelta(hours=3)
    date_str = dt_ist.strftime("%Y-%m-%d")
    weekday = dt_ist.weekday()
    mins_ist = dt_ist.hour * 60 + dt_ist.minute

    open_mins = 9 * 60 + 15
    cutoff_mins = ENTRY_CUTOFF_IST_HOUR * 60 + ENTRY_CUTOFF_IST_MINUTE
    eod_sq_mins = EOD_SQUAREOFF_IST_HOUR * 60 + EOD_SQUAREOFF_IST_MINUTE
    close_mins = 15 * 60 + 30

    is_holiday = date_str in NSE_HOLIDAYS_YYYY_MM_DD
    is_weekend = weekday >= 5
    if is_holiday:
        state = "HOLIDAY (NSE CLOSED)"
        is_open = False
    elif is_weekend:
        state = "WEEKEND (NSE CLOSED)"
        is_open = False
    elif open_mins <= mins_ist <= close_mins:
        if mins_ist >= eod_sq_mins:
            state = "OPEN (EOD SQUARE-OFF >= 15:15 IST)"
        elif mins_ist >= cutoff_mins:
            state = f"OPEN (NO NEW ENTRIES >= {ENTRY_CUTOFF_IST_HOUR:02d}:{ENTRY_CUTOFF_IST_MINUTE:02d} IST)"
        else:
            state = "OPEN (ACTIVE SESSION)"
        is_open = True
    elif mins_ist < open_mins:
        state = "PRE-OPEN / CLOSED"
        is_open = False
    else:
        state = "POST-CLOSE / CLOSED"
        is_open = False

    can_open_new_entries = is_open and (open_mins <= mins_ist < cutoff_mins)
    is_eod_squareoff = (not is_holiday and not is_weekend) and (mins_ist >= eod_sq_mins)

    return {
        "is_open": is_open,
        "can_open_new_entries": can_open_new_entries,
        "is_eod_squareoff": is_eod_squareoff,
        "state": state,
        "date_ist": date_str,
        "time_ist": dt_ist.strftime("%H:%M:%S IST"),
        "time_iraq": dt_iraq.strftime("%H:%M:%S (Iraq +03)"),
        "session_window_ist": f"09:15 - 15:30 IST (Entries < {ENTRY_CUTOFF_IST_HOUR:02d}:{ENTRY_CUTOFF_IST_MINUTE:02d} IST)",
        "session_window_iraq": "06:45 - 13:00 (Iraq +03)",
    }


def _fetch_real_ohlcv_df(sym: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    cache_key = f"{sym}|{period}|{interval}"
    now = time.monotonic()
    cached = _HISTORY_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < 60.0:
        return cached["df"].copy()
    if not _yf_allowed():
        return None
    try:
        df = yf.Ticker(sym).history(period=period, interval=interval)
        if df is not None and len(df) >= 2:
            _HISTORY_CACHE[cache_key] = {"ts": now, "df": df.copy()}
            return df
    except Exception as exc:
        _mark_yf_rate_limited(exc)
    return None


IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in NSE_HOLIDAYS_YYYY_MM_DD


def count_trading_days(start: date, end: date) -> int:
    """Trading sessions in (start, end]  (entry day itself is not counted)."""
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        if _is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def _roll_back_to_trading_day(d: date) -> date:
    while not _is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_expiry(idx_root: str, now_ist: datetime) -> datetime:
    """NIFTY: weekly Tuesday. BANKNIFTY: monthly, last Tuesday. Expiry at 15:30 IST; holiday -> previous trading day."""
    def at_close(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST_TZ)

    if idx_root == "NIFTY":
        d = now_ist.date()
        for _ in range(30):
            if d.weekday() == 1:
                exp = _roll_back_to_trading_day(d)
                if at_close(exp) > now_ist:
                    return at_close(exp)
            d += timedelta(days=1)
    y, m = now_ist.year, now_ist.month
    for _ in range(3):
        last_day = calendar.monthrange(y, m)[1]
        d = date(y, m, last_day)
        while d.weekday() != 1:
            d -= timedelta(days=1)
        exp = _roll_back_to_trading_day(d)
        if at_close(exp) > now_ist:
            return at_close(exp)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return at_close(now_ist.date() + timedelta(days=7))


def is_expiry_day(idx_root: str, now_ist: Optional[datetime] = None) -> bool:
    now_ist = now_ist or datetime.now(timezone.utc).astimezone(IST_TZ)
    return next_expiry(idx_root, now_ist).date() == now_ist.date()


def _estimate_index_option_quote(sym: str, spot_map: Optional[Dict[str, float]] = None) -> Optional[Dict[str, float]]:
    """Black-Scholes premium from live index spot + India VIX (as IV) + real time-to-expiry.
    High/low premium are the premiums at the index's 1-minute high/low (wick-accurate for stops)."""
    clean = sym.strip().upper().replace("_", " ")
    parts = clean.split()
    if len(parts) < 3 or parts[0] not in ("NIFTY", "BANKNIFTY") or parts[-1] not in ("CE", "PE"):
        return None
    try:
        strike = float(parts[-2])
    except ValueError:
        return None
    idx_root, opt = parts[0], parts[-1]
    yf_ticker = "^NSEI" if idx_root == "NIFTY" else "^NSEBANK"
    now_ist = datetime.now(timezone.utc).astimezone(IST_TZ)

    qs = get_live_quotes_batch([yf_ticker, "^INDIAVIX"], force_live=True)
    idx_q, vix_q = qs.get(yf_ticker, {}), qs.get("^INDIAVIX", {})
    idx_stale = str(idx_q.get("feed", "")) in STALE_FEEDS or float(idx_q.get("ltp", 0) or 0) < 1000
    vix_val = float(vix_q.get("ltp", 0) or 0)
    vix_stale = str(vix_q.get("feed", "")) in STALE_FEEDS or not (3.0 < vix_val < 100.0)
    if idx_stale:
        spot = float((spot_map or {}).get(yf_ticker) or (24852.40 if idx_root == "NIFTY" else 53240.15))
        idx_q = {"ltp": spot, "chg_pct": 0.0, "bar_high": spot, "bar_low": spot, "pdh": spot, "pdl": spot, "atr": spot * 0.01, "rsi": 55.0, "rvol": 1.0, "adx": 20.0}
    spot = float(idx_q["ltp"])
    iv = (FALLBACK_VIX if vix_stale else vix_val) / 100.0 * FNO_IV_SKEW.get(idx_root, 1.0)
    t_years = max((next_expiry(idx_root, now_ist) - now_ist).total_seconds(), 60.0) / (365.0 * 86400.0)

    def px(s_: float, tt: float = t_years) -> float:
        return round(max(0.05, black_scholes_price(float(s_), strike, tt, iv, opt)), 2)

    hi_s = max(float(idx_q.get("bar_high", spot)), spot)
    lo_s = min(float(idx_q.get("bar_low", spot)), spot)
    ltp = px(spot)
    bar_high, bar_low = (px(hi_s), px(lo_s)) if opt == "CE" else (px(lo_s), px(hi_s))
    chg = float(idx_q.get("chg_pct", 0.0))
    prev_spot = spot / (1.0 + chg / 100.0) if chg > -99 else spot
    prev_ltp = px(prev_spot, t_years + 1.0 / 365.0)
    pdh_s, pdl_s = float(idx_q.get("pdh", spot)), float(idx_q.get("pdl", spot))
    pdh, pdl = (px(pdh_s), px(pdl_s)) if opt == "CE" else (px(pdl_s), px(pdh_s))
    delta = abs(black_scholes_delta(spot, strike, t_years, iv, opt))
    rsi_u = float(idx_q.get("rsi", 55.0))
    return {
        "ltp": ltp, "chg_pct": round(((ltp - prev_ltp) / max(prev_ltp, 1.0)) * 100.0, 2),
        "pdh": pdh, "pdl": pdl, "bar_high": bar_high, "bar_low": bar_low,
        "rsi": rsi_u if opt == "CE" else round(100.0 - rsi_u, 2),
        "rvol": float(idx_q.get("rvol", 1.0)), "atr": round(max(4.0, delta * float(idx_q.get("atr", spot * 0.01))), 2),
        "adx": float(idx_q.get("adx", 20.0)), "vwap": ltp,
        "spot": round(spot, 2), "iv": round(iv, 4), "t_days": round(t_years * 365.0, 3), "delta": delta,
        "feed": "index_spot_option_model_stale" if (idx_stale or vix_stale) else "index_option_bs_model",
    }


COMMODITY_PROXIES: Dict[str, tuple] = {
    "GOLDPETAL MCX": ("GC=F", "gold"),
    "GOLDM MCX": ("GC=F", "gold10"),
    "CRUDEOILM MCX": ("CL=F", "crude"),
}


def _to_inr(kind: str, usd: Any, fx: Any) -> Any:
    if kind == "crude":
        return usd * fx
    per_gram = usd / 31.1035 * fx * GOLD_IMPORT_DUTY_FACTOR
    return per_gram * 10.0 if kind == "gold10" else per_gram


def _naive_dates(index: Any) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return (idx.tz_localize(None) if idx.tz is None else idx.tz_convert(None)).normalize()


def _fetch_commodity_proxy_quote(sym: str) -> Optional[Dict[str, float]]:
    spec = COMMODITY_PROXIES.get(sym.strip().upper())
    if not spec or not _yf_allowed():
        return None
    yf_t, kind = spec
    d = _fetch_real_ohlcv_df(yf_t, period="1y", interval="1d")
    fx = _fetch_real_ohlcv_df("USDINR=X", period="1y", interval="1d")
    if d is None or fx is None or len(d) < 20 or len(fx) < 5:
        return None
    fx_s = pd.Series(fx["Close"].astype(float).values, index=_naive_dates(fx.index))
    fx_s = fx_s[~fx_s.index.duplicated(keep="last")].sort_index()
    fx_al = fx_s.reindex(_naive_dates(d.index), method="ffill").bfill().values
    conv = pd.DataFrame({
        "Open": _to_inr(kind, d["Open"].astype(float).values, fx_al), "High": _to_inr(kind, d["High"].astype(float).values, fx_al),
        "Low": _to_inr(kind, d["Low"].astype(float).values, fx_al), "Close": _to_inr(kind, d["Close"].astype(float).values, fx_al),
        "Volume": d["Volume"].astype(float).values,
    }, index=d.index)
    ind = analyze_ohlcv_indicators(conv)
    m = _fetch_real_ohlcv_df(yf_t, period="1d", interval="1m")
    if m is not None and len(m) >= 1:
        fx_last = float(fx_al[-1])
        ind["ltp"] = round(float(_to_inr(kind, float(m["Close"].iloc[-1]), fx_last)), 2)
        ind["bar_high"] = round(float(_to_inr(kind, float(m["High"].iloc[-1]), fx_last)), 2)
        ind["bar_low"] = round(float(_to_inr(kind, float(m["Low"].iloc[-1]), fx_last)), 2)
        prev_c = float(ind.get("prev_close", ind["ltp"]))
        ind["chg_pct"] = round(((ind["ltp"] - prev_c) / max(prev_c, 0.01)) * 100.0, 2)
    ind["feed"] = "yfinance_commodity_proxy"
    return ind


def _refine_intraday_fields(q_obj: Dict[str, Any], sub_1d: pd.DataFrame, sub_1m: Optional[pd.DataFrame]) -> None:
    """Replace the two daily-bar approximations with session-aware values:
    VWAP = today's 1-minute session VWAP; RVOL = today's volume projected to a full day."""
    try:
        if sub_1m is None or len(sub_1m) < 5 or float(sub_1m["Volume"].astype(float).sum()) <= 0:
            return
        q_obj["vwap"] = compute_vwap(sub_1m)
        now_ist = datetime.now(timezone.utc).astimezone(IST_TZ)
        last_daily = _naive_dates(sub_1d.index)[-1].date()
        mins = now_ist.hour * 60 + now_ist.minute
        if last_daily == now_ist.date() and 9 * 60 + 15 <= mins < 15 * 60 + 30 and len(sub_1d) >= 6:
            vol = sub_1d["Volume"].astype(float)
            avg20 = float(vol.iloc[max(0, len(vol) - 21):-1].mean())
            frac = min(1.0, max(0.10, (mins - (9 * 60 + 15)) / 375.0))
            if avg20 > 0 and float(vol.iloc[-1]) > 0:
                q_obj["rvol"] = round((float(vol.iloc[-1]) / frac) / avg20, 2)
    except Exception:
        pass


def get_swing_regime() -> Dict[str, Any]:
    """Live Nifty regime for the Swing desk. Fails closed (UNKNOWN -> no new swing entries) without live index data."""
    qs = get_live_quotes_batch(["^NSEI", "^INDIAVIX"], force_live=True)
    n, v = qs.get("^NSEI", {}), qs.get("^INDIAVIX", {})
    if str(n.get("feed", "")) in STALE_FEEDS or float(n.get("ltp", 0) or 0) < 1000:
        return {"mode": "UNKNOWN", "defensive": True, "reasons": ["no live Nifty data"], "close": 0, "sma20": None, "chg_pct": 0.0, "vix": None}
    df = _fetch_real_ohlcv_df("^NSEI", period="3mo", interval="1d")
    if df is None or len(df) < 20:
        return {"mode": "UNKNOWN", "defensive": True, "reasons": ["Nifty history unavailable"], "close": float(n["ltp"]), "sma20": None, "chg_pct": float(n.get("chg_pct", 0.0)), "vix": None}
    vix = float(v["ltp"]) if (str(v.get("feed", "")) not in STALE_FEEDS and 3.0 < float(v.get("ltp", 0) or 0) < 100.0) else None
    return classify_swing_regime(float(n["ltp"]), float(df["Close"].tail(20).mean()), float(n.get("chg_pct", 0.0)), vix)


def _fetch_live_yfinance_quote(sym: str) -> Optional[Dict[str, float]]:
    if not _yf_allowed() or not (sym.endswith(".NS") or sym.startswith("^")):
        return None
    df_daily = _fetch_real_ohlcv_df(sym, period="3mo", interval="1d")
    if df_daily is None or len(df_daily) < 2:
        return None
    ind = analyze_ohlcv_indicators(df_daily)
    df_1m = _fetch_real_ohlcv_df(sym, period="1d", interval="1m")
    if df_1m is not None and len(df_1m) >= 1:
        ind["ltp"] = round(float(df_1m["Close"].iloc[-1]), 2)
        ind["bar_high"] = round(float(df_1m["High"].iloc[-1]), 2)
        ind["bar_low"] = round(float(df_1m["Low"].iloc[-1]), 2)
        prev_c = float(ind.get("prev_close", ind["ltp"]))
        ind["chg_pct"] = round(((ind["ltp"] - prev_c) / max(prev_c, 0.01)) * 100.0, 2)
        _refine_intraday_fields(ind, df_daily, df_1m)
    ind["feed"] = "yfinance_live"
    return ind


def _slice_batch(batch: Any, sym: str) -> Optional[pd.DataFrame]:
    """Extract one ticker's OHLCV from a yf.download() result (single or multi-ticker layout)."""
    try:
        if batch is None or batch.empty:
            return None
        sub = batch[sym] if isinstance(batch.columns, pd.MultiIndex) else batch
        sub = sub.dropna(subset=["Close", "High", "Low"])
        return sub if len(sub) else None
    except Exception:
        return None


def get_live_quotes_batch(
    symbols: List[str],
    default_entries: Optional[Dict[str, float]] = None,
    force_live: bool = True,
) -> Dict[str, Dict[str, float]]:
    default_entries = default_entries or {}
    now = time.monotonic()
    result: Dict[str, Dict[str, float]] = {}
    to_fetch_ns: List[str] = []

    for raw_sym in symbols:
        sym = str(raw_sym).strip().upper()
        if not sym or sym in result:
            continue
        cached = _QUOTE_CACHE.get(sym)
        if cached and (now - cached["ts"]) < QUOTE_CACHE_TTL_SECONDS:
            result[sym] = dict(cached["quote"])
        elif sym.endswith(".NS") or sym.startswith("^"):
            to_fetch_ns.append(sym)

    if force_live and to_fetch_ns and _yf_allowed():
        try:
            batch_1m = yf.download(tickers=to_fetch_ns, period="1d", interval="1m", group_by="ticker", progress=False, threads=False)
            batch_1d = yf.download(tickers=to_fetch_ns, period="1y", interval="1d", group_by="ticker", progress=False, threads=False)
            for sym in list(to_fetch_ns):
                try:
                    sub_1d = _slice_batch(batch_1d, sym)
                    if sub_1d is None or len(sub_1d) < 2:
                        continue
                    q_obj = analyze_ohlcv_indicators(sub_1d)
                    sub_1m = _slice_batch(batch_1m, sym)
                    if sub_1m is not None and len(sub_1m) >= 1:
                        q_obj["ltp"] = round(float(sub_1m["Close"].iloc[-1]), 2)
                        q_obj["bar_high"] = round(float(sub_1m["High"].iloc[-1]), 2)
                        q_obj["bar_low"] = round(float(sub_1m["Low"].iloc[-1]), 2)
                        prev_c = float(q_obj.get("prev_close", q_obj["ltp"]))
                        q_obj["chg_pct"] = round(((q_obj["ltp"] - prev_c) / max(prev_c, 0.01)) * 100.0, 2)
                        _refine_intraday_fields(q_obj, sub_1d, sub_1m)
                        q_obj["feed"] = "yfinance_batch_1m"
                    else:
                        q_obj["feed"] = "yfinance_batch_daily"
                    _QUOTE_CACHE[sym] = {"ts": now, "quote": dict(q_obj)}
                    result[sym] = q_obj
                    to_fetch_ns.remove(sym)
                except Exception:
                    pass
        except Exception as exc:
            _mark_yf_rate_limited(exc)

    for raw_sym in symbols:
        sym = str(raw_sym).strip().upper()
        if not sym or sym in result:
            continue
        live_q = (_fetch_live_yfinance_quote(sym) or _estimate_index_option_quote(sym) or _fetch_commodity_proxy_quote(sym)) if force_live else None
        if live_q is not None:
            _QUOTE_CACHE[sym] = {"ts": now, "quote": dict(live_q)}
            result[sym] = live_q
            continue
        if sym in FALLBACK_QUOTES:
            fb = dict(FALLBACK_QUOTES[sym])
            fb["feed"] = "fallback_calibrated"
            _QUOTE_CACHE[sym] = {"ts": now, "quote": dict(fb)}
            result[sym] = fb
            continue
        base_entry = float(default_entries.get(sym, 100.0) or 100.0)
        calc_q = {
            "ltp": round(base_entry, 2), "chg_pct": 0.0,
            "pdh": round(base_entry * 1.01, 2), "pdl": round(base_entry * 0.99, 2),
            "bar_high": round(base_entry, 2), "bar_low": round(base_entry, 2),
            "rsi": 55.0, "rvol": 1.25, "atr": round(base_entry * 0.015, 2),
            "adx": 20.0, "vwap": round(base_entry, 2), "feed": "entry_anchor_fallback",
        }
        _QUOTE_CACHE[sym] = {"ts": now, "quote": dict(calc_q)}
        result[sym] = calc_q
    return result


def get_quote_snapshot(symbol: str, default_entry: float = 100.0, prefer_live: bool = True) -> Dict[str, float]:
    sym = symbol.strip().upper()
    return get_live_quotes_batch([sym], default_entries={sym: default_entry}, force_live=prefer_live)[sym]


def get_market_regime() -> Dict[str, Any]:
    q = get_live_quotes_batch(["^NSEI", "^INDIAVIX"], force_live=True)
    nifty_q = q.get("^NSEI", {})
    vix_q = q.get("^INDIAVIX", {})
    vix_val = float(vix_q.get("ltp", 13.40))
    vix_display = round(vix_val, 2) if vix_val < 100 else 13.40
    nifty_chg = float(nifty_q.get("chg_pct", 0.62))
    regime = "BULLISH / LOW VIX" if (nifty_chg >= 0 and vix_display < 18.0) else ("VOLATILE / HIGH VIX" if vix_display >= 18.0 else "CAUTIOUS / RANGE")
    return {
        "regime": regime, "vix": vix_display,
        "breadth_adv_pct": 68 if nifty_chg >= 0 else 42,
        "cooldown": "OFF", "stale_feeds": 0,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_ticker_strip() -> List[Dict[str, Any]]:
    syms = ["^NSEI", "^NSEBANK", "^INDIAVIX", "RELIANCE.NS", "TATASTEEL.NS", "GOLDPETAL MCX", "CRUDEOILM MCX"]
    q = get_live_quotes_batch(syms, force_live=True)
    nifty_p = q["^NSEI"]["ltp"] if q["^NSEI"]["ltp"] > 1000 else 24852.40
    bn_p = q["^NSEBANK"]["ltp"] if q["^NSEBANK"]["ltp"] > 1000 else 53240.15
    vix_p = q["^INDIAVIX"]["ltp"] if q["^INDIAVIX"]["ltp"] < 100 else 13.40
    return [
        {"sym": "NIFTY 50", "price": nifty_p, "chg": q["^NSEI"]["chg_pct"]},
        {"sym": "BANK NIFTY", "price": bn_p, "chg": q["^NSEBANK"]["chg_pct"]},
        {"sym": "INDIA VIX", "price": vix_p, "chg": q["^INDIAVIX"]["chg_pct"]},
        {"sym": "GOLD ₹/g (proxy)", "price": q["GOLDPETAL MCX"]["ltp"], "chg": q["GOLDPETAL MCX"]["chg_pct"]},
        {"sym": "CRUDE ₹/bbl (proxy)", "price": q["CRUDEOILM MCX"]["ltp"], "chg": q["CRUDEOILM MCX"]["chg_pct"]},
        {"sym": "RELIANCE", "price": q["RELIANCE.NS"]["ltp"], "chg": q["RELIANCE.NS"]["chg_pct"]},
        {"sym": "TATASTEEL", "price": q["TATASTEEL.NS"]["ltp"], "chg": q["TATASTEEL.NS"]["chg_pct"]},
    ]


def get_ohlcv_bars(symbol: str, base_price: float, count: int = 32) -> List[Dict[str, Any]]:
    raw_sym = str(symbol or "").strip().upper()
    sym = raw_sym
    if sym.startswith("NIFTY "):
        sym = "^NSEI"
    elif sym.startswith("BANKNIFTY "):
        sym = "^NSEBANK"

    if sym.endswith(".NS") or sym.startswith("^"):
        df = _fetch_real_ohlcv_df(sym, period="3mo", interval="1d")
        if df is not None and len(df) >= 2:
            d = df.copy()
            idx = pd.DatetimeIndex(d.index)
            if idx.tz is not None:
                idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
            else:
                idx = idx.tz_localize(None)
            d.index = idx
            d = d[~d.index.duplicated(keep="last")].sort_index()
            d = d[d.index >= pd.Timestamp("2020-01-01")]
            d = d.dropna(subset=["Open", "High", "Low", "Close"])
            if len(d) >= 2:
                tail_df = d.tail(count)
                bars: List[Dict[str, Any]] = []
                for ts_idx, row in tail_df.iterrows():
                    has_time = (ts_idx.hour != 0 or ts_idx.minute != 0)
                    dt_str = ts_idx.strftime("%Y-%m-%d %H:%M") if has_time else ts_idx.strftime("%Y-%m-%d")
                    label = ts_idx.strftime("%b %d %H:%M") if has_time else ts_idx.strftime("%b %d, %Y")
                    bars.append({
                        "dt": dt_str,
                        "bar": label,
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
                        "vol": round(float(row.get("Volume", 0.0) or 0.0), 1),
                    })
                return bars

    px = round(float(base_price or 100.0), 2)
    end_dt = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).replace(tzinfo=None).normalize()
    b_dates = pd.bdate_range(end=end_dt, periods=max(2, int(count)))
    return [
        {
            "dt": d.strftime("%Y-%m-%d"),
            "bar": d.strftime("%b %d, %Y"),
            "open": px,
            "high": round(px * 1.004, 2),
            "low": round(px * 0.996, 2),
            "close": px,
            "vol": 0.0,
        }
        for d in b_dates
    ]
```[cite: 57, 95]
