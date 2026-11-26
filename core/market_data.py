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
```[cite: 2, 39]

---

### 2. Full `core/ui_components.py`

```python
import time
import json
from typing import Any, Dict, List
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.attribution import compute_desk_and_agent_trust
from core.charges import compute_net_rr
from core.config import normalize_desk_key, get_desk_config, get_effective_desk_config, parse_desk_settings
from core.market_data import get_market_regime, get_ohlcv_bars, get_quote_snapshot, get_ticker_strip
from core.sizing import compute_position_sizing


DARK_OBS_CSS = """
<style>
  .stApp { background-color: #0B1120; color: #F3F4F6; }
  [data-testid="stSidebar"] { background-color: #111827; border-right: 1px solid #1E293B; }
  .v48-topbar { background: #111827; border: 1px solid #1E293B; border-radius: 14px; padding: 14px 18px; margin-bottom: 14px; }
  .v48-pill { display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-family: monospace; font-weight: 600; margin-right: 6px; }
  .pill-cyan { background: rgba(6,182,212,0.15); color: #67E8F9; border: 1px solid rgba(6,182,212,0.35); }
  .pill-green { background: rgba(16,185,129,0.15); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.35); }
  .pill-amber { background: rgba(245,158,11,0.15); color: #FCD34D; border: 1px solid rgba(245,158,11,0.35); }
  .pill-rose { background: rgba(244,63,94,0.15); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.35); }
  .v48-kpi { background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 14px 16px; }
  .v48-kpi-label { font-size: 12px; color: #94A3B8; }
  .v48-kpi-val { font-size: 22px; font-weight: 700; font-family: monospace; color: #F8FAFC; margin-top: 4px; }
  .v48-kpi-sub { font-size: 11px; font-family: monospace; color: #6EE7B7; margin-top: 3px; }
  .badge-tier-high { background: rgba(244,63,94,0.18); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-tier-mid  { background: rgba(245,158,11,0.18); color: #FCD34D; border: 1px solid rgba(245,158,11,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-tier-low  { background: rgba(16,185,129,0.18); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-prot-lock { background: rgba(16,185,129,0.18); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.45); padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
  .badge-prot-risk { background: rgba(244,63,94,0.18); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.45); padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
</style>
"""


def inject_theme() -> None:
    st.markdown(DARK_OBS_CSS, unsafe_allow_html=True)


def render_top_command_bar(ledger_source: str = "google_sheets", status_msg: str = "", **kwargs: Any) -> None:
    inject_theme()
    reg = get_market_regime()
    src_badge = (
        f"<span class='v48-pill pill-green'>{status_msg or 'Single Ledger: Ledger'}</span>"
        if ledger_source == "google_sheets"
        else "<span class='v48-pill pill-amber'>Single Ledger: Local Snapshot</span>"
    )
    st.markdown(
        f"<div class='v48-topbar'>"
        f"<div style='display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;'>"
        f"<div><span style='font-weight:700;font-size:17px;color:#FFFFFF;'>NSE Multi-Asset AI Trading Platform</span>"
        f"<span class='v48-pill pill-cyan' style='margin-left:8px;'>Schema: v4.8</span>{src_badge}</div>"
        f"<div><span class='v48-pill pill-green'>Regime: {reg['regime']} ({reg['vix']}) | Breadth: {reg['breadth_adv_pct']}% Adv</span>"
        f"<span class='v48-pill pill-amber'>PAPER MODE — Broker Parked (live_mode=False)</span></div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    tickers = get_ticker_strip()
    cols = st.columns(len(tickers))
    for idx, t in enumerate(tickers):
        up = t["chg"] >= 0
        color = "#34D399" if up else "#FB7185"
        cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:8px 10px;'>"
            f"<div style='font-size:10px;color:#94A3B8;'>{t['sym']}</div>"
            f"<div style='font-size:13px;font-weight:700;font-family:monospace;color:#FFFFFF;'>{t['price']:,.2f}</div>"
            f"<div style='font-size:10px;font-family:monospace;color:{color};'>{'+' if up else ''}{t['chg']:.2f}%</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _style_watch_dataframe(df: pd.DataFrame) -> Any:
    def style_tier(val: Any) -> str:
        v = str(val).upper()
        if v == "HIGH":
            return "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"
        if v == "LOW":
            return "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;"
        return "background-color: rgba(245,158,11,0.22); color: #FCD34D; font-weight: 700;"

    def style_protection(val: Any) -> str:
        return "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;" if str(val).startswith("Locked") else "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"

    def style_signed(val: Any) -> str:
        try:
            num = float(str(val).replace("%", "").replace("+", "").replace(",", ""))
            if num > 0:
                return "color: #34D399; font-weight: 700;"
            if num < 0:
                return "color: #FB7185; font-weight: 700;"
        except Exception:
            pass
        return "color: #E2E8F0;"

    return df.style.map(style_tier, subset=["TIER"]).map(style_protection, subset=["PROTECTION"]).map(style_signed, subset=["PROG %", "MTM GROSS ₹", "MTM NET ₹"])


def render_filter_watch_table(open_trades: List[Dict[str, Any]], key_prefix: str = "main", on_close_callback: Any = None) -> None:
    st.subheader("Filter Watch Table")
    c_filt, c_view = st.columns([3, 1])
    with c_filt:
        filter_choice = st.radio("Filter Watch Table", ["All Positions", "Locked Profit Only", "At Risk / Breached Only"], horizontal=True, label_visibility="collapsed", key=f"{key_prefix}_watch_filter")
    with c_view:
        compact_cards = st.toggle("Compact Card View", value=False, key=f"{key_prefix}_compact_cards")

    filtered = [t for t in open_trades if t.get("is_locked")] if filter_choice == "Locked Profit Only" else ([t for t in open_trades if not t.get("is_locked")] if filter_choice == "At Risk / Breached Only" else open_trades)
    if not filtered:
        st.info("No open positions match this filter.")
        return

    if compact_cards:
        cols = st.columns(2)
        for idx, t in enumerate(filtered):
            tier = str(t.get("tier", "MID")).upper()
            tier_cls = "badge-tier-high" if tier == "HIGH" else ("badge-tier-low" if tier == "LOW" else "badge-tier-mid")
            prot_cls = "badge-prot-lock" if t.get("is_locked") else "badge-prot-risk"
            mtm = float(t.get("mtm_pnl", 0.0))
            mtm_col = "#34D399" if mtm >= 0 else "#FB7185"
            sym_clean = str(t["symbol"]).replace(".NS", "")
            with cols[idx % 2]:
                st.markdown(
                    f"<div class='v48-kpi' style='margin-bottom:10px;'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<strong style='font-family:monospace;color:#FFFFFF;'>{sym_clean} ({t.get('desk','Swing')})</strong>"
                    f"<span class='{tier_cls}'>{tier}</span></div>"
                    f"<div style='font-size:12px;font-family:monospace;color:#CBD5E1;margin-top:6px;'>"
                    f"Qty {int(t['qty']):,} | Entry ₹{float(t['entry']):,.2f} → LTP ₹{float(t['ltp']):,.2f}</div>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:8px;'>"
                    f"<span class='{prot_cls}'>{t['protection_label']}</span>"
                    f"<span style='font-family:monospace;font-weight:700;color:{mtm_col};'>Net {'+' if mtm >= 0 else ''}{mtm:,.2f}</span></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        rows = [{
            "SYMBOL": str(t["symbol"]).replace(".NS", ""),
            "DESK": t.get("desk", "Swing"),
            "TIER": str(t.get("tier", "MID")).upper(),
            "QTY": f"{int(t['qty']):,}",
            "ENTRY ₹": f"{float(t['entry']):,.2f}",
            "INIT SL ₹": f"{float(t.get('initial_sl', t['sl'])):,.2f}",
            "LTP ₹": f"{float(t['ltp']):,.2f}",
            "STOP ₹": f"{float(t['sl']):,.2f}",
            "TARGET ₹": f"{float(t['target']):,.2f}",
            "PROG %": f"{float(t.get('prog_pct', 0.0)):+.1f}%",
            "PROTECTION": t["protection_label"],
            "MTM GROSS ₹": f"{float(t.get('mtm_gross', t.get('mtm_pnl', 0.0))):+,.2f}",
            "MTM NET ₹": f"{float(t.get('mtm_pnl', 0.0)):+,.2f}",
        } for t in filtered]
        st.dataframe(_style_watch_dataframe(pd.DataFrame(rows)), width="stretch", hide_index=True, height=min(740, 44 + len(rows) * 35))

    if on_close_callback is not None and filtered:
        with st.expander("Manual Paper Exit Controls"):
            sel_sym = st.selectbox("Select open position to close at current LTP (net of charges):", [f"{t['symbol']} | {t.get('desk','Swing')} | Qty {t['qty']} | Net MTM ₹{t['mtm_pnl']:+,.2f}" for t in filtered], key=f"{key_prefix}_close_select")
            if st.button("Confirm Paper Exit", key=f"{key_prefix}_close_btn", type="primary"):
                chosen_symbol = sel_sym.split("|")[0].strip()
                res = on_close_callback(chosen_symbol)
                if res.get("closed"):
                    st.success(f"Closed {chosen_symbol} at LTP | Net P&L: ₹{res['net_pnl']:+,.2f} ({res['realized_r']:+.2f}R)")
                    st.rerun()


def render_desk_and_agent_scorecards(trust_data: Dict[str, Any]) -> None:
    st.subheader(f"Multi-Desk & 5-Agent Circular Trust Scorecards (Weighted Composite: {trust_data['composite_trust']}/100)")
    desk_cols = st.columns(4)
    for idx, desk_name in enumerate(["Swing", "Intraday", "F&O", "Commodity"]):
        d = trust_data["desks"][desk_name]
        t_val = d["trust_score"]
        delta = d["delta"]
        ring_col = "#34D399" if t_val >= 65 else ("#38BDF8" if t_val >= 50 else "#FBBF24")
        desk_cols[idx].markdown(
            f"<div class='v48-kpi'>"
            f"<div class='v48-kpi-label'>{desk_name} Desk Trust (50 Base)</div>"
            f"<div class='v48-kpi-val' style='color:{ring_col};'>{t_val}/100 <span style='font-size:12px;'>({delta:+.1f})</span></div>"
            f"<div class='v48-kpi-sub'>Closed {d['closed_count']} | Win {d['win_rate']}% | Avg {d['avg_r']:+.2f}R | Net ₹{d['realized_net_pnl']:+,.2f}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    ag_cols = st.columns(5)
    for idx, ag in enumerate(trust_data["agents"]):
        ag_cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:10px 12px;'>"
            f"<div style='font-size:10px;color:#67E8F9;font-family:monospace;'>{ag['agent_id']}</div>"
            f"<div style='font-size:17px;font-weight:700;font-family:monospace;color:#FFFFFF;margin-top:2px;'>{ag['trust_score']}/100 ({ag['delta']:+.1f})</div>"
            f"<div style='font-size:10px;color:#94A3B8;'>{ag['label']} ({ag['tier']})</div>"
            f"<div style='font-size:10px;color:#6EE7B7;font-family:monospace;margin-top:2px;'>Samples {ag.get('samples', ag.get('closed_count', 0))} | Win {ag['win_rate']}% | Avg {ag['avg_r']:+.2f}R</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def render_desk_individual_agent_trust(desk_label: str, d_trust: Dict[str, Any]) -> None:
    st.subheader(f"{desk_label} Desk — Individual Agent Trust Scores (Desk Overall: {d_trust['trust_score']}/100, {d_trust['delta']:+.1f})")
    agents = d_trust.get("agents", [])
    if not agents:
        st.info(f"No agent trust breakdown available for {desk_label}.")
        return
    cols = st.columns(len(agents))
    for idx, ag in enumerate(agents):
        t_val = ag["trust_score"]
        col_hex = "#34D399" if t_val >= 65 else ("#38BDF8" if t_val >= 50 else "#FBBF24")
        cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:10px 12px;'>"
            f"<div style='font-size:10px;color:#67E8F9;font-family:monospace;'>{ag['agent_id']}</div>"
            f"<div style='font-size:18px;font-weight:700;font-family:monospace;color:{col_hex};margin-top:2px;'>{t_val}/100 ({ag['delta']:+.1f})</div>"
            f"<div style='font-size:10px;color:#94A3B8;'>{ag['label']} ({ag['tier']})</div>"
            f"<div style='font-size:10px;color:#6EE7B7;font-family:monospace;margin-top:2px;'>Closed {ag['closed_count']} | Win {ag['win_rate']}% | Avg {ag['avg_r']:+.2f}R</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _message_matches_scope(msg: Dict[str, Any], scope_desk: str) -> bool:
    sc = (scope_desk or "ALL").strip().upper()
    if sc in ("", "ALL", "DASHBOARD"):
        return True
    blob = " ".join([str(msg.get(k, "")) for k in ("cycle_id", "sender", "recipient", "msg_type", "summary", "payload_json", "desk")]).lower()
    if sc == "SWING":
        return ("swing" in blob) or ("nse_5agent_ledger" in blob)
    if sc == "INTRADAY":
        return ("intraday" in blob) or ("intra-" in blob) or ("iwd-" in blob) or ("stepped_floor" in blob)
    if sc in ("FNO", "F&O"):
        return ("fno" in blob) or ("f&o" in blob) or ("nifty" in blob) or ("banknifty" in blob) or (" ce " in f" {blob} ") or (" pe " in f" {blob} ")
    if sc == "COMMODITY":
        return ("commodity" in blob) or ("mcx" in blob) or ("goldm" in blob) or ("crudeoilm" in blob)
    if sc == "AUDIT":
        return any(k in blob for k in ("audit", "backtest", "time_machine", "stage_audit", "verdict", "rejected", "approved", "gate_blocked"))
    if sc == "POSTTRADE":
        return any(k in blob for k in ("exit", "closed", "trailing_sl", "sl_hit", "target_hit", "squareoff", "attribution", "pnl", "realized_r", "watchdog", "ledger"))
    return True


def render_agent_war_room(messages: List[Dict[str, Any]], limit: int = 15, scope_desk: str = "ALL") -> None:
    sc = (scope_desk or "ALL").strip().upper()
    title_map = {
        "ALL": "5-Agent Council & Watchdog Deliberation Feed (Both Ledgers)",
        "SWING": "5-Agent Council & Watchdog Deliberation Feed — Swing Desk",
        "INTRADAY": "5-Agent Council & Watchdog Deliberation Feed — Intraday Desk",
        "FNO": "5-Agent Council & Watchdog Deliberation Feed — F&O Desk",
        "COMMODITY": "5-Agent Council & Watchdog Deliberation Feed — Commodity Desk",
        "AUDIT": "5-Agent Council & Watchdog Deliberation Feed — Audit & Backtest",
        "POSTTRADE": "5-Agent Council & Watchdog Deliberation Feed — Post-Trade & Attribution",
    }
    st.subheader(title_map.get(sc, "5-Agent Council & Watchdog Deliberation Feed"))
    filtered = [m for m in messages if _message_matches_scope(m, sc)]
    recent = sorted(filtered, key=lambda m: str(m.get("timestamp", "")), reverse=True)[:limit]
    if not recent:
        st.info(f"No agent messages logged yet for {sc}.")
        return
    for m in recent:
        sender = str(m.get("sender", ""))
        pill_cls = "pill-green" if ("AG4" in sender or "AG5" in sender) else ("pill-amber" if "WATCHDOG" in sender else ("pill-rose" if "HIGH" in sender else "pill-cyan"))
        st.markdown(
            f"<div class='v48-kpi' style='padding:10px 14px;margin-bottom:8px;'>"
            f"<div style='display:flex;flex-wrap:wrap;justify-content:space-between;font-size:11px;font-family:monospace;'>"
            f"<div><span class='v48-pill {pill_cls}'>{sender}</span>"
            f"<span style='color:#94A3B8;'>→ {m.get('recipient','')} | {m.get('msg_type','')}</span></div>"
            f"<span style='color:#64748B;'>{m.get('cycle_id','')} | {m.get('timestamp','')}</span></div>"
            f"<div style='font-size:12.5px;color:#E2E8F0;margin-top:5px;'>{m.get('summary','')}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def render_market_clock_and_manual_runner(default_desk: str = "ALL") -> None:
    from core.market_data import get_nse_session_status
    from headless_runner import trigger_manual_agent_cycle

    if "cas_enabled" not in st.session_state:
        st.session_state["cas_enabled"] = True

    sess = get_nse_session_status()
    badge_color = "#10B981" if sess["is_open"] else "#F59E0B"
    cas_color = "#10B981" if st.session_state["cas_enabled"] else "#EF4444"
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:8px 12px;"
        f"border-radius:8px;background:#0F172A;border:1px solid #1E293B;margin-bottom:10px;font-family:monospace;font-size:12px;'>"
        f"<span style='color:{badge_color};font-weight:700;'>● NSE: {sess['state']}</span>"
        f"<span style='color:#E2E8F0;'>IST: {sess['time_ist']}</span>"
        f"<span style='color:#93C5FD;'>Iraq: {sess['time_iraq']}</span>"
        f"<span style='color:{cas_color};font-weight:700;'>CAS: {'ON' if st.session_state['cas_enabled'] else 'PAUSED'}</span>"
        f"<span style='color:#94A3B8;'>Hours: {sess['session_window_ist']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Manual Agent Cycle, CAS Toggle & Watchdog Trigger", expanded=False):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
        desk_opts = ["ALL", "SWING", "INTRADAY", "FNO", "COMMODITY"]
        idx_default = desk_opts.index(default_desk.upper()) if default_desk.upper() in desk_opts else 0
        sel_desk = c1.selectbox("Target Desk", desk_opts, index=idx_default, key=f"man_desk_{default_desk}")
        max_ent = c2.number_input("Max Entries", min_value=1, max_value=5, value=1, step=1, key=f"man_ent_{default_desk}")
        st.session_state["cas_enabled"] = c3.checkbox("CAS Active", value=st.session_state["cas_enabled"], key=f"cas_chk_{default_desk}")
        if c4.button("Run Agent Cycle Now", type="primary", key=f"man_run_btn_{default_desk}"):
            with st.spinner("Running agent cycle..."):
                res = trigger_manual_agent_cycle(desk=sel_desk, max_entries=int(max_ent), watchdog_bursts=1, ignore_session_gate=True, cas_enabled=bool(st.session_state["cas_enabled"]))
            st.session_state["last_manual_cycle_res"] = res
            st.rerun()
        if "last_manual_cycle_res" in st.session_state:
            _errs = st.session_state["last_manual_cycle_res"].get("errors", [])
            for _e in _errs:
                st.error(f"{_e['desk']}: {_e['error']}")
            if not _errs:
                st.success("Completed manual cycle (Paper Mode: LIVE_MODE=false) — Ledger & Feed synced.")
            with st.expander("Last Manual Cycle JSON Output", expanded=False):
                st.json(st.session_state["last_manual_cycle_res"])


def _desk_trades(trades: List[Dict[str, Any]], cfg: Any, open_only: bool) -> List[Dict[str, Any]]:
    out = []
    for t in trades:
        if normalize_desk_key(t.get("desk", "Swing")) != cfg.key:
            continue
        if (str(t.get("status", "")).upper() == "OPEN") == open_only:
            out.append(t)
    return out


def derive_free_cash(cfg: Any, trades: List[Dict[str, Any]]) -> float:
    """Free cash = configured capital + realized P&L - cash blocked by open positions (margin-aware)."""
    realized = sum(float(t.get("pnl", 0.0) or 0.0) for t in _desk_trades(trades, cfg, open_only=False))
    blocked = sum(float(t.get("entry", 0.0) or 0.0) * int(float(t.get("qty", 0) or 0)) * cfg.margin_pct for t in _desk_trades(trades, cfg, open_only=True))
    return max(0.0, cfg.capital + realized - blocked)


def render_capital_settings(store: Any, accounts: Dict[str, Any], key_prefix: str = "dash") -> None:
    """Editable per-desk settings that are SAVED to the ledger `account` tab; the headless runner reads them every cycle."""
    st.caption("Saved to the ledger and used by the GitHub-Actions runner on its next cycle (not just this browser session).")
    new_settings: Dict[str, Dict[str, Any]] = {}
    for key in ("swing", "intraday", "fno", "commodity"):
        cfg = get_effective_desk_config(key, accounts)
        st.markdown(f"**{cfg.label}**")
        c1, c2, c3, c4, c5 = st.columns(5)
        cap = c1.number_input("Capital (₹)", min_value=0.0, step=10000.0, value=float(cfg.capital), key=f"{key_prefix}_{key}_cap")
        mp = c2.number_input("Max per position (%)", min_value=1.0, max_value=100.0, step=1.0, value=float(cfg.max_pos_pct), key=f"{key_prefix}_{key}_mp")
        rk = c3.number_input("Risk per trade (%)", min_value=0.1, max_value=5.0, step=0.1, value=float(cfg.risk_per_trade_pct), key=f"{key_prefix}_{key}_rk")
        cu = c4.number_input("Soft cushion (%)", min_value=0.0, max_value=40.0, step=1.0, value=float(cfg.soft_cushion_pct), key=f"{key_prefix}_{key}_cu")
        sl = c5.number_input("Baseline slots", min_value=1, max_value=100, step=1, value=int(cfg.baseline_slots), key=f"{key_prefix}_{key}_sl")
        new_settings[key] = {"capital": cap, "max_pos_pct": mp, "risk_per_trade_pct": rk, "soft_cushion_pct": cu, "baseline_slots": sl}
    if st.button("💾 Save settings for the runner", type="primary", key=f"{key_prefix}_save_settings"):
        store.update_account_state({"desk_settings": json.dumps(new_settings)})
        st.success("Saved. The next agent cycle will size positions with these values.")
        st.rerun()


def render_desk_workspace(store: Any, state: Dict[str, Any], desk_key: str, default_chart_symbol: str) -> None:
    accounts = state.get("accounts", {})
    cfg = get_effective_desk_config(desk_key, accounts)
    all_trades = state["trades"]
    desk_open = [t for t in all_trades if t["status"] == "OPEN" and normalize_desk_key(t.get("desk", "Swing")) == cfg.key]
    desk_closed = [t for t in all_trades if t["status"] != "OPEN" and normalize_desk_key(t.get("desk", "Swing")) == cfg.key]
    trust_data = compute_desk_and_agent_trust(all_trades)
    d_trust = trust_data["desks"][cfg.label]

    st.header(f"{cfg.label} Desk — Multi-Asset V4.8 Workspace")

    c1, c2, c3, c4, c5 = st.columns(5)
    cap = c1.number_input(f"{cfg.label} Capital (₹)", min_value=0.0, step=10000.0, value=float(cfg.capital), key=f"{cfg.key}_cap")
    max_pos = c2.number_input("Max Per-Pos Cap (%)", min_value=2.0, max_value=100.0, step=1.0, value=float(cfg.max_pos_pct), key=f"{cfg.key}_maxpos")
    risk_pct = c3.number_input("Risk Per Trade (%)", min_value=0.1, max_value=5.0, step=0.1, value=float(cfg.risk_per_trade_pct), key=f"{cfg.key}_risk")
    cushion_pct = c4.number_input("Soft Cushion (%)", min_value=0.0, max_value=40.0, step=1.0, value=float(cfg.soft_cushion_pct), key=f"{cfg.key}_cush")
    allow_tap = c5.checkbox("Allow Cushion Tap", value=cfg.allow_cushion_tap, key=f"{cfg.key}_tap")
    if st.button(f"💾 Save as {cfg.label} runner settings", key=f"{cfg.key}_save_ws"):
        merged = parse_desk_settings(accounts.get("desk_settings"))
        merged[cfg.key] = {"capital": cap, "max_pos_pct": max_pos, "risk_per_trade_pct": risk_pct, "soft_cushion_pct": cushion_pct, "allow_cushion_tap": allow_tap}
        store.update_account_state({"desk_settings": json.dumps(merged)})
        st.success("Saved - the runner uses these on its next cycle.")
        st.rerun()

    deployed = sum(float(t["entry"]) * int(t["qty"]) * cfg.margin_pct for t in desk_open)
    unreal_net = sum(float(t.get("mtm_pnl", 0.0)) for t in desk_open)
    real = sum(float(t.get("pnl", 0.0)) for t in desk_closed)

    raw_avail = max(0.0, cap + real - deployed)
    soft_buf = cap * (cushion_pct / 100.0)

    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Available Cash (Ledger)", f"₹{raw_avail:,.2f}", f"Buffer ₹{soft_buf:,.0f}")
    s2.metric("Open Deployed", f"₹{deployed:,.2f}", f"{len(desk_open)} / {cfg.baseline_slots} Slots")
    s3.metric("Unrealized Net MTM", f"₹{unreal_net:+,.2f}")
    s4.metric("Realized P&L (Closed)", f"₹{real:+,.2f}", f"{len(desk_closed)} Closed Trades")
    s5.metric(f"{cfg.label} Trust Score", f"{d_trust['trust_score']}/100 ({d_trust['delta']:+.1f})", f"Win {d_trust['win_rate']}% | Avg {d_trust['avg_r']:+.2f}R")

    render_desk_individual_agent_trust(cfg.label, d_trust)

    chart_syms = [t["symbol"] for t in desk_open] or [t["symbol"] for t in desk_closed] or [default_chart_symbol]
    sel_chart_sym = st.selectbox("Select Chart & Live Sizer Symbol", list(dict.fromkeys(chart_syms)), key=f"{cfg.key}_chart_sym")
    q = get_quote_snapshot(sel_chart_sym, default_entry=100.0)
    lot_sz = 1
    sizer_entry = float(q["ltp"])
    sizer_sl = round(max(1.0, sizer_entry - max(0.5, float(q.get("atr", sizer_entry * 0.015)))), 2)
    sizer_tgt = round(sizer_entry + 2.0 * abs(sizer_entry - sizer_sl), 2)
    sizing = compute_position_sizing(
        desk_capital=cap, deployed_capital=deployed, entry_price=sizer_entry, stop_loss=sizer_sl, target_price=sizer_tgt,
        conviction_score=85.0, max_pos_pct=max_pos, risk_per_trade_pct=risk_pct, soft_cushion_pct=cushion_pct,
        baseline_slots=cfg.baseline_slots, current_open_slots=len(desk_open), allow_cushion_tap=allow_tap,
        allow_overflow_slots=cfg.allow_overflow_slots, lot_size=lot_sz,
        max_overflow_slots=cfg.max_overflow_slots, margin_pct=cfg.margin_pct,
    )
    net_rr = compute_net_rr(sizer_entry, sizer_sl, sizer_tgt, max(1, sizing["qty"]), desk=cfg.label)
    st.info(
        f"**Live Sizer ({sel_chart_sym}):** LTP `₹{q['ltp']:,.2f}` | Raw Risk Qty `{sizing['raw_risk_qty']}` -> Final Qty `{sizing['qty']}` (Lot `{lot_sz}`) | "
        f"Notional `₹{sizing['notional']:,.0f}` | Net R:R `1:{net_rr:.2f}` | Tag: **`{sizing['allocation_tag']}`**"
    )

    bars = pd.DataFrame(get_ohlcv_bars(sel_chart_sym, q["ltp"], count=32))
    x_vals = pd.to_datetime(bars["dt"], errors="coerce") if "dt" in bars.columns else bars["bar"]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=x_vals,
                open=bars["open"],
                high=bars["high"],
                low=bars["low"],
                close=bars["close"],
                name=sel_chart_sym,
            )
        ]
    )
    fig.add_hline(y=q["pdh"], line_dash="dash", line_color="#C084FC", annotation_text=f"PDH ₹{q['pdh']}")
    fig.add_hline(y=q["pdl"], line_dash="dash", line_color="#C084FC", annotation_text=f"PDL ₹{q['pdl']}")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0B1120",
        plot_bgcolor="#0F172A",
        height=350,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(type="date", tickformat="%b %d"),
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, width="stretch")

    render_filter_watch_table(open_trades=desk_open, key_prefix=cfg.key, on_close_callback=lambda sym: store.close_paper_position(sym))

    if desk_closed:
        st.subheader(f"{cfg.label} Closed / Exited Trades ({len(desk_closed)})")
        cols_show = ["timestamp", "exit_timestamp", "symbol", "tier", "status", "qty", "entry", "initial_sl", "exit_price", "gross_pnl", "charges_slippage", "pnl", "realized_r", "reason"]
        df_c = pd.DataFrame(desk_closed)
        st.dataframe(df_c[[c for c in cols_show if c in df_c.columns]], width="stretch", hide_index=True)

    render_agent_war_room(state["agent_messages"], limit=10, scope_desk=cfg.key.upper())


def render_auto_refresh_control(default_enabled: bool = False, interval_sec: int = 20, key_prefix: str = "main") -> None:
    """Optional polling loop that respects the 20s ledger cache TTL."""
    interval_sec = max(20, int(interval_sec))
    c1, c2 = st.columns([1, 3])
    enabled = c1.toggle("Auto-Refresh", value=default_enabled, key=f"{key_prefix}_autorefresh")
    sec = c2.number_input("Interval (sec)", min_value=20, max_value=300, value=interval_sec, step=5, key=f"{key_prefix}_refresh_sec")
    if enabled:
        time.sleep(int(sec))
        st.rerun()
```[cite: 2, 32]
