import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

try:
    import yfinance as yf
except Exception:
    yf = None

from core.market_data import STALE_FEEDS, _yf_allowed, get_live_quotes_batch, get_quote_snapshot
from core.quant_engine import score_setup_from_indicators

EQUITY_UNIVERSE: List[Dict[str, Any]] = [
    {"symbol": "RELIANCE.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "ENERGY", "beta": 0.96},
    {"symbol": "TCS.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "IT", "beta": 0.82},
    {"symbol": "INFY.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "IT", "beta": 0.91},
    {"symbol": "TECHM.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "IT", "beta": 0.95},
    {"symbol": "WIPRO.NS", "desks": ["INTRADAY"], "tier": "LOW", "sector": "IT", "beta": 0.98},
    {"symbol": "HDFCBANK.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "BANKING", "beta": 1.04},
    {"symbol": "ICICIBANK.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "BANKING", "beta": 1.12},
    {"symbol": "SBIN.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "BANKING", "beta": 1.24},
    {"symbol": "FEDERALBNK.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "BANKING", "beta": 1.18},
    {"symbol": "INDUSINDBK.NS", "desks": ["INTRADAY"], "tier": "HIGH", "sector": "BANKING", "beta": 1.46},
    {"symbol": "BAJFINANCE.NS", "desks": ["SWING", "INTRADAY"], "tier": "HIGH", "sector": "FINANCE", "beta": 1.32},
    {"symbol": "ICICIGI.NS", "desks": ["SWING"], "tier": "MID", "sector": "INSURANCE", "beta": 0.92},
    {"symbol": "LICI.NS", "desks": ["SWING"], "tier": "MID", "sector": "INSURANCE", "beta": 0.95},
    {"symbol": "SUNPHARMA.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "PHARMA", "beta": 0.78},
    {"symbol": "CIPLA.NS", "desks": ["SWING"], "tier": "LOW", "sector": "PHARMA", "beta": 0.80},
    {"symbol": "DRREDDY.NS", "desks": ["SWING"], "tier": "LOW", "sector": "PHARMA", "beta": 0.84},
    {"symbol": "TORNTPHARM.NS", "desks": ["SWING"], "tier": "LOW", "sector": "PHARMA", "beta": 0.76},
    {"symbol": "LUPIN.NS", "desks": ["SWING"], "tier": "MID", "sector": "PHARMA", "beta": 0.94},
    {"symbol": "TVSMOTOR.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "AUTO", "beta": 1.18},
    {"symbol": "M&M.NS", "desks": ["SWING", "INTRADAY"], "tier": "MID", "sector": "AUTO", "beta": 1.14},
    {"symbol": "TATASTEEL.NS", "desks": ["INTRADAY"], "tier": "HIGH", "sector": "METALS", "beta": 1.38},
    {"symbol": "JSWSTEEL.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "METALS", "beta": 1.28},
    {"symbol": "LT.NS", "desks": ["SWING", "INTRADAY"], "tier": "LOW", "sector": "INFRA", "beta": 1.05},
    {"symbol": "ADANIENT.NS", "desks": ["SWING", "INTRADAY"], "tier": "HIGH", "sector": "CONGLOMERATE", "beta": 1.56},
    {"symbol": "DIXON.NS", "desks": ["SWING"], "tier": "HIGH", "sector": "ELECTRONICS", "beta": 1.52},
    {"symbol": "SRF.NS", "desks": ["SWING"], "tier": "LOW", "sector": "CHEMICALS", "beta": 0.98},
    {"symbol": "GRASIM.NS", "desks": ["SWING"], "tier": "MID", "sector": "CEMENT", "beta": 1.02},
    {"symbol": "DABUR.NS", "desks": ["SWING"], "tier": "MID", "sector": "FMCG", "beta": 0.74},
    {"symbol": "ONGC.NS", "desks": ["SWING"], "tier": "LOW", "sector": "ENERGY", "beta": 1.08},
    {"symbol": "COALINDIA.NS", "desks": ["SWING"], "tier": "LOW", "sector": "MINING", "beta": 1.04},
    {"symbol": "TATAPOWER.NS", "desks": ["SWING"], "tier": "MID", "sector": "POWER", "beta": 1.22},
    {"symbol": "PFC.NS", "desks": ["SWING"], "tier": "MID", "sector": "FINANCE", "beta": 1.34},
]

COMMODITY_UNIVERSE: List[Dict[str, Any]] = [
    {"symbol": "GOLDPETAL MCX", "desk": "COMMODITY", "tier": "MID", "sector": "BULLION", "lot_size": 1, "beta": 0.65},   # INR/gram, lot 1 g
    {"symbol": "CRUDEOILM MCX", "desk": "COMMODITY", "tier": "HIGH", "sector": "ENERGY", "lot_size": 10, "beta": 1.15},  # INR/bbl, lot 10 bbl
]


def check_earnings_hours(sym: str) -> int:
    if not _yf_allowed() or not sym.endswith(".NS"):
        return 360
    try:
        cal = yf.Ticker(sym).calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                ed = ed[0]
            if ed is not None and hasattr(ed, "year"):
                dt_ed = datetime(ed.year, ed.month, ed.day, tzinfo=timezone.utc)
                diff_h = int((dt_ed - datetime.now(timezone.utc)).total_seconds() // 3600)
                if diff_h >= 0:
                    return diff_h
    except Exception:
        pass
    return 360


_check_earnings_hours = check_earnings_hours   # backwards-compatible alias


def build_dynamic_fno_option_candidates() -> List[Dict[str, Any]]:
    """ATM option on the index trend side. Premiums are Black-Scholes (live spot + India VIX + real expiry);
    lot sizes are the real contract sizes. Direction needs index trend + ADX; chop -> low conviction (rejected)."""
    from datetime import datetime, timezone
    from core.config import FNO_EXPIRY_NO_BUY_IST, FNO_LOT_SIZES, FNO_MIN_ADX, FNO_SL_PCT, FNO_TARGET_RR
    from core.market_data import IST_TZ, is_expiry_day

    spots = get_live_quotes_batch(["^NSEI", "^NSEBANK"], force_live=True)
    now_ist = datetime.now(timezone.utc).astimezone(IST_TZ)
    after_expiry_cutoff = (now_ist.hour, now_ist.minute) >= FNO_EXPIRY_NO_BUY_IST
    out: List[Dict[str, Any]] = []
    for idx_name, ticker, step in (("NIFTY", "^NSEI", 50), ("BANKNIFTY", "^NSEBANK", 100)):
        iq = spots.get(ticker, {})
        spot = float(iq.get("ltp", 0) or 0)
        if str(iq.get("feed", "")) in STALE_FEEDS or spot < 1000:
            continue                                   # no live index data -> no option candidate
        if after_expiry_cutoff and is_expiry_day(idx_name, now_ist):
            continue                                   # expiry-day theta cliff: no new buying after 13:30 IST
        chg, adx = float(iq.get("chg_pct", 0.0)), float(iq.get("adx", 20.0))
        ema9, ema20 = float(iq.get("ema9", spot)), float(iq.get("ema20", spot))
        up = spot > ema20 and chg >= 0
        down = spot < ema20 and chg < 0
        trending = adx >= FNO_MIN_ADX and (up or down)
        side = "CE" if (up or (not down and chg >= 0)) else "PE"
        conviction = 55                                # range/chop: below the 65 gate
        if trending:
            conviction = 72 + (6 if adx >= 25 else 0) + (4 if (ema9 > ema20) == (side == "CE") else 0)
        atm = int(round(spot / step) * step)
        sym = f"{idx_name} {atm} {side}"
        q = get_quote_snapshot(sym, default_entry=150.0 if idx_name == "NIFTY" else 600.0, prefer_live=True)
        entry = round(float(q["ltp"]), 2)
        sl_dist = round(max(4.0, entry * FNO_SL_PCT), 2)
        out.append({
            "symbol": sym, "desk": "FNO", "tier": "HIGH", "sector": "INDEX",
            "entry": entry, "sl": round(max(2.0, entry - sl_dist), 2), "target": round(entry + sl_dist * FNO_TARGET_RR, 2),
            "conviction": min(90, conviction), "earnings_hours": 999,
            "lot_size": FNO_LOT_SIZES[idx_name], "beta": 1.38, "atr": round(sl_dist, 2),
            "feed": str(q.get("feed", "")),
            "rsi": float(q.get("rsi", 55.0)), "rvol": float(q.get("rvol", 1.0)), "adx": adx, "vwap": entry,
            "pdh": float(q.get("pdh", entry * 1.08)), "pdl": float(q.get("pdl", entry * 0.90)),
            "rationale": (f"{idx_name} {spot:,.0f} ({chg:+.2f}%), ADX {adx:.0f}, {'trend-aligned' if trending else 'RANGE/CHOP'}; "
                          f"ATM {atm} {side} ₹{entry:,.2f}, IV {q.get('iv', 0):.2%}, {q.get('t_days', 0):.1f}d to expiry, lot {FNO_LOT_SIZES[idx_name]}."),
        })
    return out


def get_desk_candidates(desk_upper: str) -> List[Dict[str, Any]]:
    from core.config import normalize_desk_key
    d = normalize_desk_key(desk_upper).upper()
    if d == "FNO":
        return build_dynamic_fno_option_candidates()
    if d == "COMMODITY":
        out_c: List[Dict[str, Any]] = []
        for item in COMMODITY_UNIVERSE:
            sym = item["symbol"]
            q = get_quote_snapshot(sym, default_entry=7842.0 if "GOLD" in sym else 5865.0, prefer_live=True)
            scored = score_setup_from_indicators(q, desk="COMMODITY")
            out_c.append({
                "symbol": sym, "desk": "COMMODITY", "tier": item["tier"], "sector": item["sector"],
                "entry": scored["entry"], "sl": scored["sl"], "target": scored["target"],
                "conviction": scored["conviction"], "earnings_hours": 999,
                "lot_size": item["lot_size"], "beta": item["beta"],
                "atr": q["atr"], "rsi": q["rsi"], "rvol": q["rvol"], "adx": q["adx"],
                "vwap": q["vwap"], "pdh": q["pdh"], "pdl": q["pdl"], "rationale": scored["rationale"],
                "feed": q.get("feed", ""),
            })
        out_c.sort(key=lambda x: x["conviction"], reverse=True)
        return out_c

    matched_meta = [m for m in EQUITY_UNIVERSE if d in m["desks"]]
    syms = [m["symbol"] for m in matched_meta]
    quotes = get_live_quotes_batch(syms, force_live=True)

    candidates: List[Dict[str, Any]] = []
    for m in matched_meta:
        sym = m["symbol"]
        q = quotes[sym]
        scored = score_setup_from_indicators(q, desk=d)
        candidates.append({
            "symbol": sym, "desk": d, "tier": m["tier"], "sector": m["sector"],
            "entry": scored["entry"], "sl": scored["sl"], "target": scored["target"],
            "conviction": scored["conviction"], "earnings_hours": 360, "earnings_checked": False,   # looked up lazily, only for candidates that pass the cheap gates
            "lot_size": 1, "beta": m["beta"],
            "atr": q["atr"], "rsi": q["rsi"], "rvol": q["rvol"], "adx": q["adx"],
            "vwap": q["vwap"], "pdh": q["pdh"], "pdl": q["pdl"], "rationale": scored["rationale"],
            "feed": q.get("feed", ""),
        })
    candidates.sort(key=lambda x: x["conviction"], reverse=True)
    return candidates


_GEMINI_BAD_UNTIL: float = 0.0
_NEG_PATTERN = re.compile(r"(fraud|scam|ban|banned|probe|downgrade|default|lawsuit|crash|penalty|insolvency)", flags=re.IGNORECASE)
_POS_PATTERN = re.compile(r"(surge|beats|order win|profit jumps|upgrade|expansion|dividend|record high|breakout)", flags=re.IGNORECASE)


def fetch_news_sentiment(symbol: str, limit: int = 3) -> Dict[str, Any]:
    clean_query = symbol.replace(".NS", "").replace(" MCX", "") + " NSE stock"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_query)}&hl=en-IN&gl=IN&ceid=IN:en"
    headlines: List[str] = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:limit]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                headlines.append(title_el.text.strip())
    except Exception:
        headlines = []

    joined = " ".join(headlines)
    if _NEG_PATTERN.search(joined):
        status = "NEGATIVE_RISK"
    elif _POS_PATTERN.search(joined):
        status = "POSITIVE_CATALYST"
    else:
        status = "CLEAR"

    return {"symbol": symbol, "news_status": status, "headlines": headlines, "source": "google_news_rss" if headlines else "default_clear"}


def evaluate_with_gemini_if_configured(candidate: Dict[str, Any], quote: Dict[str, Any], news: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    global _GEMINI_BAD_UNTIL
    if time.time() < _GEMINI_BAD_UNTIL:
        return {"ai_used": False, "news_status": news["news_status"], "conviction_adj": 0, "note": "Gemini paused after a recent error (deterministic screen used)."}
    if not api_key or genai is None or types is None:
        return {"ai_used": False, "news_status": news["news_status"], "conviction_adj": 0, "note": candidate.get("rationale", "Deterministic V4.8 quantitative/news screen.")}
    try:
        try:
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(os.getenv("GEMINI_TIMEOUT_MS", "8000"))))
        except Exception:
            client = genai.Client(api_key=api_key)
        prompt = (
            f"Evaluate NSE paper trade candidate {candidate['symbol']} on desk {candidate['desk']} "
            f"(tier {candidate['tier']}, entry {candidate['entry']}, SL {candidate['sl']}, target {candidate['target']}, "
            f"LTP {quote.get('ltp')}, RSI {quote.get('rsi')}, RVOL {quote.get('rvol')}, ADX {quote.get('adx')}, "
            f"news_status={news['news_status']}, headlines={news['headlines'][:2]}). "
            f"Return JSON with keys: news_status (CLEAR|POSITIVE_CATALYST|NEGATIVE_RISK), conviction_adj (-10 to +10), rationale."
        )
        resp = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
        )
        data = json.loads(resp.text or "{}")
        st_val = str(data.get("news_status", news["news_status"])).upper()
        if st_val not in ("CLEAR", "POSITIVE_CATALYST", "NEGATIVE_RISK"):
            st_val = news["news_status"]
        adj = max(-10, min(10, int(data.get("conviction_adj", 0) or 0)))
        return {"ai_used": True, "news_status": st_val, "conviction_adj": adj, "note": str(data.get("rationale", "Gemini structured screen complete."))}
    except Exception as exc:
        _GEMINI_BAD_UNTIL = time.time() + 600          # stop calling Gemini for 10 minutes after a failure
        return {"ai_used": False, "news_status": news["news_status"], "conviction_adj": 0, "note": f"Fallback screen after Gemini error: {exc}"}
