import os
import io
import json
import time
import asyncio
import urllib.request
import http.cookiejar
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import pandas as pd
import yfinance as yf
import feedparser
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
from google.genai.errors import APIError
from schemas import StockSignal, CommitteeOutput, FinalDecision

IST = timezone(timedelta(hours=5, minutes=30))
CANDIDATE_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
SWING_SHEET_NAME = "NSE_5Agent_Ledger"

# --- V3.1 PORTFOLIO & RISK GUARDRAILS ---
MAX_OPEN_POSITIONS = 15
MIN_ORDER_VALUE = 25_000.0
DEAD_MONEY_MAX_DAYS = 7
RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}
ATR_MULT_PER_TIER = {"LOW": 1.5, "MID": 2.0, "HIGH": 1.0}
TIER_TO_AGENT = {"LOW": "AG1_LOW_RISK", "MID": "AG2_MID_RISK", "HIGH": "AG3_HIGH_RISK"}

SECTOR_INDICES = {
    "BANK": "^NSEBANK",
    "IT": "^CNXIT",
    "AUTO": "^CNXAUTO",
    "PHARMA": "^CNXPHARMA",
    "METAL": "^CNXMETAL",
    "ENERGY": "^CNXENERGY",
}

FALLBACK_NSE_250 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS", "INFY.NS", "SBIN.NS",
    "HINDUNILVR.NS", "ITC.NS", "LT.NS", "HCLTECH.NS", "BAJFINANCE.NS", "SUNPHARMA.NS", "MARUTI.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "TITAN.NS", "ONGC.NS", "TATAMOTORS.NS", "ADANIENT.NS", "NTPC.NS",
    "ULTRACEMCO.NS", "M&M.NS", "POWERGRID.NS", "WIPRO.NS", "COALINDIA.NS", "BAJAJFINSV.NS", "JSWSTEEL.NS",
    "TATASTEEL.NS", "ADANIPORTS.NS", "ASIANPAINT.NS", "DMART.NS", "HAL.NS", "BEL.NS", "TRENT.NS",
    "SIEMENS.NS", "VBL.NS", "NESTLEIND.NS", "GRASIM.NS", "TECHM.NS", "INDIGO.NS", "DLF.NS", "HINDALCO.NS",
    "DIVISLAB.NS", "PIDILITIND.NS", "LTIM.NS", "SBILIFE.NS", "HDFCLIFE.NS", "BAJAJ-AUTO.NS", "BRITANNIA.NS",
    "EICHERMOT.NS", "CIPLA.NS", "DRREDDY.NS", "APOLLOHOSP.NS", "TATACONSUM.NS", "HEROMOTOCO.NS", "BPCL.NS",
    "IOC.NS", "GAIL.NS", "INDUSINDBK.NS", "GODREJCP.NS", "BANKBARODA.NS", "PNB.NS", "CANBK.NS",
    "UNIONBANK.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "MUTHOOTFIN.NS", "RECLTD.NS", "PFC.NS", "IRFC.NS",
    "RVNL.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BDL.NS", "BHEL.NS", "SAIL.NS", "NMDC.NS", "VEDL.NS",
    "HINDZINC.NS", "NATIONALUM.NS", "JINDALSTEL.NS", "APLAPOLLO.NS", "POLYCAB.NS", "KEI.NS", "HAVELLS.NS",
    "VOLTAS.NS", "BLUESTARCO.NS", "DIXON.NS", "KAYNES.NS", "AMBER.NS", "CROMPTON.NS", "ABB.NS",
    "CUMMINSIND.NS", "THERMAX.NS", "CGPOWER.NS", "SUZLON.NS", "TATAPOWER.NS", "JSWENERGY.NS", "NHPC.NS",
    "SJVN.NS", "ADANIPOWER.NS", "ADANIGREEN.NS", "ADANIENSOL.NS", "ATGL.NS", "TORNTPOWER.NS", "CESC.NS",
    "PERSISTENT.NS", "COFORGE.NS", "MPHASIS.NS", "KPITTECH.NS", "TATAELXSI.NS", "LTTS.NS", "OFSS.NS",
    "NAUKRI.NS", "POLICYBZR.NS", "PAYTM.NS", "DELHIVERY.NS", "IRCTC.NS", "CONCOR.NS", "GMRINFRA.NS",
    "OBEROIRLTY.NS", "GODREJPROP.NS", "PRESTIGE.NS", "PHOENIXLTD.NS", "LODHA.NS", "BRIGADE.NS",
    "SOBHA.NS", "LUPIN.NS", "AUROPHARMA.NS", "ALKEM.NS", "TORNTPHARM.NS", "BIOCON.NS", "GLENMARK.NS",
    "LAURUSLABS.NS", "IPCALAB.NS", "ZYDUSLIFE.NS", "MAXHEALTH.NS", "FORTIS.NS", "SYNGENE.NS",
    "TVSMOTOR.NS", "ASHOKLEY.NS", "BHARATFORG.NS", "MOTHERSON.NS", "SONACOMS.NS", "BALKRISIND.NS",
    "MRF.NS", "APOLLOTYRE.NS", "EXIDEIND.NS", "AMARAJABAT.NS", "ESCORTS.NS", "BOSCHLTD.NS",
    "MARICO.NS", "DABUR.NS", "COLPAL.NS", "UBL.NS", "UNITDSPR.NS", "RADICO.NS", "JUBLFOOD.NS",
    "DEVYANI.NS", "KALYANKJIL.NS", "TITAGARH.NS", "TEXRAIL.NS", "JWL.NS", "KPIL.NS", "KEC.NS",
    "NCC.NS", "NBCC.NS", "HUDCO.NS", "IRCON.NS", "RITES.NS", "ENGINERSIN.NS", "GSPL.NS", "PETRONET.NS",
    "IGL.NS", "MGL.NS", "GUJGASLTD.NS", "DEEPAKNTR.NS", "SRF.NS", "PIIND.NS", "UPL.NS", "ATUL.NS",
    "NAVINFLUOR.NS", "AARTIIND.NS", "TATACHEM.NS", "CHAMBLFERT.NS", "COROMANDEL.NS", "ASTRAL.NS",
    "SUPREMEIND.NS", "FINPIPE.NS", "KAJARIACER.NS", "CENTURYPLY.NS", "PAGEIND.NS", "ABFRL.NS",
    "MANYAVAR.NS", "BATAINDIA.NS", "RELAXO.NS", "INDHOTEL.NS", "EIHOTEL.NS", "LEMONTREE.NS",
    "FEDERALBNK.NS", "IDFCFIRSTB.NS", "AUBANK.NS", "BANDHANBNK.NS", "RBLBANK.NS", "KARURVYSYA.NS",
    "CUB.NS", "ABCAPITAL.NS", "LTF.NS", "MANAPPURAM.NS", "POONAWALLA.NS", "IIFL.NS", "360ONE.NS",
    "ANGELONE.NS", "CDSL.NS", "BSE.NS", "MCX.NS", "CAMS.NS", "KFINTECH.NS", "NUVAMA.NS",
    "SBICARD.NS", "ICICIGI.NS", "ICICIPRULI.NS", "STARHEALTH.NS", "GICRE.NS", "NIACL.NS",
    "ACC.NS", "AMBUJACEM.NS", "SHREECEM.NS", "DALBHARAT.NS", "JKCEMENT.NS", "RAMCOCEM.NS",
    "SUNDRMFAST.NS", "ENDURANCE.NS", "UNOMINDA.NS", "SCHAEFFLER.NS", "TIMKEN.NS", "SKFINDIA.NS"
]


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def is_nse_open_now() -> bool:
    dt = now_ist()
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


def get_gsheet_client(gcp_creds_dict: dict, sheet_name: str = SWING_SHEET_NAME):
    creds_copy = dict(gcp_creds_dict)
    if "private_key" in creds_copy:
        creds_copy["private_key"] = creds_copy["private_key"].replace("\\n", "\n")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_copy, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(sheet_name)


def log_msg(sh, cycle_id: str, sender: str, recipient: str, msg_type: str, summary: str, payload):
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


# ==============================================================================
# PERSISTENT PEAK P&L + CYCLE RACE-CONDITION LOCK (CELLS B2 & C2 IN account)
# ==============================================================================
def get_persisted_peak_pnl(acct_ws) -> float:
    try:
        val = acct_ws.acell("B2").value
        return float(val) if val not in (None, "") else 0.0
    except Exception:
        return 0.0


def set_persisted_peak_pnl(acct_ws, peak_val: float):
    try:
        if not acct_ws.acell("B1").value:
            acct_ws.update("B1", [["peak_portfolio_pnl"]])
        acct_ws.update("B2", [[round(peak_val, 2)]])
    except Exception:
        pass


def acquire_cycle_lock(acct_ws, cooldown_sec: int = 90) -> bool:
    """Prevents manual click + GitHub Actions cron from double-buying simultaneously."""
    try:
        if not acct_ws.acell("C1").value:
            acct_ws.update("C1", [["last_cycle_epoch"]])
        last_val = acct_ws.acell("C2").value
        now_ep = time.time()
        if last_val not in (None, ""):
            if (now_ep - float(last_val)) < cooldown_sec:
                return False
        acct_ws.update("C2", [[round(now_ep, 2)]])
        return True
    except Exception:
        return True


# ==============================================================================
# DYNAMIC AGENT TRUST SCORES (0-100 ELO) + REFLECTION LESSONS
# ==============================================================================
def infer_trade_agent(row: dict) -> str:
    reason = str(row.get("reason", row.get("reasoning", "")))
    for ag in ("AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"):
        if f"[{ag}]" in reason or ag in reason:
            return ag
    tier = str(row.get("tier", "MID")).upper()
    return TIER_TO_AGENT.get(tier, "AG2_MID_RISK")


def compute_agent_trust_scores(trades_records: list[dict]) -> dict[str, dict]:
    stats = {
        "AG1_LOW_RISK": {"score": 50, "wins": 0, "losses": 0, "net_pnl": 0.0, "label": "AG1 (Low Risk)"},
        "AG2_MID_RISK": {"score": 50, "wins": 0, "losses": 0, "net_pnl": 0.0, "label": "AG2 (Mid Risk)"},
        "AG3_HIGH_RISK": {"score": 50, "wins": 0, "losses": 0, "net_pnl": 0.0, "label": "AG3 (High Risk)"},
    }
    for r in trades_records:
        status = str(r.get("status", ""))
        if status in ("OPEN", "", "None"):
            continue
        ag = infer_trade_agent(r)
        if ag not in stats:
            continue
        pnl = float(r.get("pnl", 0) or 0)
        entry = float(r.get("entry", 0) or 0)
        qty = float(r.get("qty", 0) or 0)
        cost = max(entry * qty, 1.0)
        ret_pct = (pnl / cost) * 100.0

        stats[ag]["net_pnl"] = round(stats[ag]["net_pnl"] + pnl, 2)
        if pnl > 0:
            stats[ag]["wins"] += 1
            delta = min(8, max(3, int(round(3 + ret_pct))))
            stats[ag]["score"] = min(100, stats[ag]["score"] + delta)
        elif pnl < 0:
            stats[ag]["losses"] += 1
            delta = min(8, max(3, int(round(3 + abs(ret_pct)))))
            stats[ag]["score"] = max(5, stats[ag]["score"] - delta)

    return stats


def record_trade_reflection(sh, cycle_id: str, agent_id: str, symbol: str, tier: str, exit_tag: str, pnl: float, entry: float, exit_px: float, old_score: int, new_score: int):
    ret_pct = ((exit_px - entry) / max(entry, 0.01)) * 100.0
    if pnl > 0:
        lesson = (
            f"[{agent_id}] {symbol} ({tier}) closed +{ret_pct:.2f}% (₹{pnl:+,.2f}) via {exit_tag}. "
            f"Momentum + VWAP confirmation worked; Trust Score {old_score} -> {new_score}."
        )
    else:
        lesson = (
            f"[{agent_id}] {symbol} ({tier}) hit {exit_tag} ({ret_pct:.2f}%, ₹{pnl:+,.2f}). "
            f"Tighten sector alignment & avoid chasing extended candles; Trust Score {old_score} -> {new_score}."
        )
    log_msg(
        sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "AGENT_LESSON", lesson,
        {"agent_id": agent_id, "symbol": symbol, "tier": tier, "exit_tag": exit_tag, "pnl": pnl, "old_score": old_score, "new_score": new_score},
    )


def get_recent_agent_lessons(sh, limit: int = 5) -> list[str]:
    try:
        rows = sh.worksheet("agent_messages").get_all_records()
        lessons = [str(r.get("summary", "")) for r in rows if r.get("msg_type") == "AGENT_LESSON"]
        return lessons[-limit:]
    except Exception:
        return []


# ==============================================================================
# MARKET REGIME + SECTOR STRENGTH + MEMORY SCORECARD
# ==============================================================================
def get_market_regime_and_sectors() -> dict:
    tickers = ["^NSEI"] + list(SECTOR_INDICES.values())
    regime = {
        "nifty_chg_pct": 0.0,
        "nifty_above_20sma": True,
        "defensive_mode": False,
        "top_sectors": [],
        "weak_sectors": [],
        "positive_sector_count": 0,
    }
    try:
        data = yf.download(tickers, period="2mo", interval="1d", group_by="ticker", progress=False, threads=True)
        if "^NSEI" in data.columns.get_level_values(0):
            n_hist = data["^NSEI"]["Close"].dropna()
            if len(n_hist) >= 20:
                curr, prev = float(n_hist.iloc[-1]), float(n_hist.iloc[-2])
                sma20 = float(n_hist.rolling(20).mean().iloc[-1])
                chg = round(((curr - prev) / prev) * 100, 2)
                regime["nifty_chg_pct"] = chg
                regime["nifty_above_20sma"] = curr >= sma20
                regime["defensive_mode"] = (chg <= -0.60) or (curr < sma20)

        sec_perf = []
        for sec_name, sym in SECTOR_INDICES.items():
            if sym in data.columns.get_level_values(0):
                s_hist = data[sym]["Close"].dropna()
                if len(s_hist) >= 2:
                    s_chg = ((float(s_hist.iloc[-1]) - float(s_hist.iloc[-2])) / float(s_hist.iloc[-2])) * 100
                    sec_perf.append((sec_name, round(s_chg, 2)))
        sec_perf.sort(key=lambda x: x[1], reverse=True)
        regime["positive_sector_count"] = sum(1 for _, c in sec_perf if c > 0)
        regime["top_sectors"] = [f"{s[0]} ({s[1]:+.2f}%)" for s in sec_perf[:2]]
        regime["weak_sectors"] = [f"{s[0]} ({s[1]:+.2f}%)" for s in sec_perf[-2:]]
    except Exception:
        pass
    return regime


def build_trade_memory_scorecard(trust_scores: dict[str, dict], lessons: list[str]) -> str:
    trust_summary = " | ".join(
        [f"{k}: Trust {v['score']}/100 ({v['wins']}W-{v['losses']}L, ₹{v['net_pnl']:+,.0f})" for k, v in trust_scores.items()]
    )
    lesson_text = " ; ".join(lessons[-3:]) if lessons else "Enforce strict R:R >= 1:1.6 and clean news."
    return f"Agent Trust Standings -> {trust_summary}. Recent Self-Reflections -> {lesson_text}"


def enrich_with_intraday_vwap(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return candidates
    syms = list({c["symbol"] for c in candidates})
    try:
        intra = yf.download(syms, period="1d", interval="15m", group_by="ticker", progress=False, threads=True)
        for c in candidates:
            sym = c["symbol"]
            c["above_vwap"] = True
            try:
                sub = intra[sym].dropna() if len(syms) > 1 else intra.dropna()
                if not sub.empty and sub["Volume"].sum() > 0:
                    typical_px = (sub["High"] + sub["Low"] + sub["Close"]) / 3.0
                    vwap = float((typical_px * sub["Volume"]).sum() / sub["Volume"].sum())
                    curr_px = float(sub["Close"].iloc[-1])
                    c["vwap"] = round(vwap, 2)
                    c["above_vwap"] = curr_px >= vwap
            except Exception:
                continue
    except Exception:
        for c in candidates:
            c["above_vwap"] = True
    vwap_approved = [c for c in candidates if c.get("above_vwap", True)]
    return vwap_approved if len(vwap_approved) >= 3 else candidates


def has_upcoming_earnings_48h(symbol: str) -> bool:
    try:
        cal = yf.Ticker(symbol).calendar
        if isinstance(cal, dict) and "Earnings Date" in cal:
            dates = cal["Earnings Date"]
            if dates:
                next_dt = pd.to_datetime(dates[0]).tz_localize(None)
                diff_hours = (next_dt - datetime.now()).total_seconds() / 3600.0
                if -12 <= diff_hours <= 48:
                    return True
    except Exception:
        pass
    return False


def fetch_nse_live_movers() -> list[str]:
    movers = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    }
    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        opener.addheaders = list(headers.items())
        opener.open("https://www.nseindia.com", timeout=6).read()

        endpoints = [
            "https://www.nseindia.com/api/live-analysis-variations?index=gainers",
            "https://www.nseindia.com/api/live-analysis-variations?index=loosers",
            "https://www.nseindia.com/api/live-analysis-volume-gainers",
        ]
        for ep in endpoints:
            try:
                raw_json = opener.open(ep, timeout=6).read().decode("utf-8")
                payload = json.loads(raw_json)
                rows = []
                if isinstance(payload, dict):
                    if "allSec" in payload and isinstance(payload["allSec"], dict):
                        rows = payload["allSec"].get("data", [])
                    elif "NIFTY" in payload and isinstance(payload["NIFTY"], dict):
                        rows = payload["NIFTY"].get("data", [])
                    elif "data" in payload:
                        rows = payload.get("data", [])
                for item in rows[:20]:
                    sym = str(item.get("symbol", "")).strip()
                    if sym and sym.isalpha():
                        movers.append(f"{sym}.NS")
            except Exception:
                continue
    except Exception:
        pass
    return list(dict.fromkeys(movers))


def get_dynamic_250_universe() -> tuple[list[str], int]:
    live_movers = fetch_nse_live_movers()
    nifty200_symbols = []
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            csv_text = resp.read().decode("utf-8")
        df_idx = pd.read_csv(io.StringIO(csv_text))
        if "Symbol" in df_idx.columns:
            nifty200_symbols = [f"{str(s).strip()}.NS" for s in df_idx["Symbol"].dropna().tolist()]
    except Exception:
        nifty200_symbols = FALLBACK_NSE_250[:200]

    combined = list(dict.fromkeys(live_movers + nifty200_symbols + FALLBACK_NSE_250))
    return combined[:250], len(live_movers)


def fetch_nse_metrics(excluded_symbols: set[str] | None = None) -> tuple[list[dict], dict[str, float], int]:
    excluded = excluded_symbols or set()
    universe, live_mover_count = get_dynamic_250_universe()
    snapshots = []
    live_prices = {}

    try:
        data = yf.download(universe, period="6mo", interval="1d", group_by="ticker", progress=False, threads=True)
    except Exception:
        return snapshots, live_prices, live_mover_count

    for sym in universe:
        try:
            if sym not in data.columns.get_level_values(0):
                continue
            hist = data[sym].dropna(subset=["Close"])
            if len(hist) < 35:
                continue

            close, volume = hist["Close"], hist["Volume"]
            ltp = float(close.iloc[-1])
            avg_vol = float(volume.rolling(20).mean().iloc[-1])

            if ltp > 0:
                live_prices[sym] = round(ltp, 2)

            if sym in excluded or ltp < 50.0 or avg_vol < 100_000:
                continue

            sma_50 = float(close.rolling(min(50, len(hist))).mean().iloc[-1])
            sma_200 = float(close.rolling(min(200, len(hist))).mean().iloc[-1])

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, float("nan"))
            rsi_val = (100 - (100 / (1 + rs))).iloc[-1]
            rsi = float(rsi_val) if rsi_val == rsi_val else 50.0

            atr = float((hist["High"] - hist["Low"]).rolling(14).mean().iloc[-1])
            rvol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

            atr_pct = (atr / ltp) * 100
            beta_proxy = round(max(0.6, min(2.2, atr_pct / 1.8)), 2)
            prev_close = float(close.iloc[-2])
            day_change_pct = round(((ltp - prev_close) / prev_close) * 100, 2)

            snapshots.append({
                "symbol": sym,
                "ltp": round(ltp, 2),
                "sma_50": round(sma_50, 2),
                "sma_200": round(sma_200, 2),
                "rsi_14": round(rsi, 2),
                "atr_14": round(atr, 2),
                "rvol": round(rvol, 2),
                "beta": beta_proxy,
                "day_change_pct": day_change_pct,
            })
        except Exception:
            continue

    return snapshots, live_prices, live_mover_count


def fetch_nse_news(symbol: str) -> list[str]:
    try:
        clean_name = symbol.replace(".NS", "")
        url = f"https://news.google.com/rss/search?q={quote(clean_name + ' NSE stock')}&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)
        return [entry.title for entry in feed.entries[:3]]
    except Exception:
        return ["No breaking news headlines"]


async def generate_fast(client, prompt: str, schema, temperature: float = 0.2):
    last_error = None
    for model_name in CANDIDATE_MODELS:
        try:
            coro = client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=temperature,
                ),
            )
            resp = await asyncio.wait_for(coro, timeout=14.0)
            return json.loads(resp.text)
        except (APIError, asyncio.TimeoutError) as e:
            last_error = e
            await asyncio.sleep(0.8)
            continue
    raise last_error


def quant_fallback_screener(agent_id: str, data_slice: list[dict]) -> list[StockSignal]:
    sorted_data = sorted(data_slice, key=lambda x: x["rvol"], reverse=True)[:2]
    signals = []
    for d in sorted_data:
        atr = max(d["atr_14"], d["ltp"] * 0.01)
        signals.append(
            StockSignal(
                agent_id=agent_id,
                symbol=d["symbol"],
                ltp=d["ltp"],
                entry_price=d["ltp"],
                stop_loss=round(d["ltp"] - 1.5 * atr, 2),
                target_price=round(d["ltp"] + 3.0 * atr, 2),
                rsi_14=d["rsi_14"],
                rvol=d["rvol"],
                beta=d["beta"],
                setup_reason=f"Quant + VWAP funnel: RVOL {d['rvol']}x, RSI {d['rsi_14']}, 1:2 R:R setup.",
            )
        )
    return signals


async def run_screener(
    sh, client, cycle_id: str, agent_id: str, rules: str,
    data_slice: list[dict], scanned_count: int, mover_count: int,
    regime: dict, trust_info: dict, lessons: list[str]
) -> list[StockSignal]:
    if not data_slice:
        log_msg(sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "NO_SIGNAL", "No setups qualified under current regime.", [])
        return []

    agent_lessons = [l for l in lessons if f"[{agent_id}]" in l]
    lesson_note = f"Your recent self-reflection: {agent_lessons[-1]}" if agent_lessons else ""

    regime_note = (
        f"Market Context: NIFTY 50 is {regime['nifty_chg_pct']:+.2f}% "
        f"({'DEFENSIVE MODE - be extra selective' if regime['defensive_mode'] else 'BULLISH/NORMAL'}). "
        f"Leading Sectors: {', '.join(regime['top_sectors'])}. "
        f"Your current Trust Score is {trust_info['score']}/100 ({trust_info['wins']}W-{trust_info['losses']}L). {lesson_note}"
    )
    prompt = (
        f"You are {agent_id} for NSE India (scanning {scanned_count} live NSE stocks including {mover_count} live intraday movers). "
        f"{regime_note} {rules} All candidates passed 15m intraday VWAP support. "
        f"Pick the 2 best distinct stocks matching your risk tier with realistic INR entry_price, stop_loss, and target_price: {json.dumps(data_slice)}"
    )
    try:
        raw = await generate_fast(client, prompt, list[StockSignal], temperature=0.2)
        signals = [StockSignal(**item) for item in raw]
        for s in signals:
            s.agent_id = agent_id
        mode_tag = ""
    except Exception:
        signals = quant_fallback_screener(agent_id, data_slice)
        mode_tag = " [Quant Engine]"

    mover_note = f" ({mover_count} live movers)" if mover_count > 0 else ""
    summary = (
        f"Scanned {scanned_count} NSE stocks{mover_note} [Trust {trust_info['score']}/100] -> Sent {len(signals)} to AG4{mode_tag}: "
        + ", ".join([f"{s.symbol} @ ₹{s.entry_price}" for s in signals])
        if signals
        else "No setups approved."
    )
    log_msg(sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "CANDIDATE_PROPOSAL", summary, [s.model_dump() for s in signals])
    return signals


async def run_ag4(sh, client, cycle_id: str, candidates: list[StockSignal], regime: dict, memory_scorecard: str, trust_scores: dict) -> CommitteeOutput:
    enriched = []
    for c in candidates:
        earnings_soon = has_upcoming_earnings_48h(c.symbol)
        news_items = fetch_nse_news(c.symbol)
        if earnings_soon:
            news_items.insert(0, "ALERT: Quarterly earnings scheduled within 48 hours - SKIP to avoid gap risk.")
        ag_trust = trust_scores.get(c.agent_id, {}).get("score", 50)
        enriched.append({
            "signal": c.model_dump(),
            "proposing_agent_trust_score": ag_trust,
            "earnings_within_48h": earnings_soon,
            "news": news_items,
        })

    regime_rule = (
        "NIFTY 50 is in DEFENSIVE MODE (down > 0.6% or below 20-SMA): approve AT MOST 1 ultra-high-conviction LOW/MID trade, or SKIP all if setups are weak."
        if regime["defensive_mode"]
        else "NIFTY 50 trend is supportive: approve 1-3 top setups with clean news, non-overbought RSI, and R:R >= 1:1.6."
    )

    prompt = (
        f"You are AG4, Chief Analyst for NSE India. {regime_rule} "
        f"{memory_scorecard} "
        "Weigh proposals according to each proposing agent's Trust Score (0-100; agents below 45 require higher conviction and cleaner setups). "
        f"Leading Sectors Today: {', '.join(regime['top_sectors'])}. "
        "Audit all candidates with their news headlines. Immediately SKIP any stock with earnings_within_48h=True or negative news. "
        "Preserve exact source_agent (AG1_LOW_RISK, AG2_MID_RISK, or AG3_HIGH_RISK) on each decision. "
        f"Inputs: {json.dumps(enriched)}"
    )
    try:
        raw = await generate_fast(client, prompt, CommitteeOutput, temperature=0.1)
        decision = CommitteeOutput(**raw)
    except Exception:
        tier_map = {"AG1_LOW_RISK": "LOW", "AG2_MID_RISK": "MID", "AG3_HIGH_RISK": "HIGH"}
        fallback_trades, seen = [], set()
        max_buys = 1 if regime["defensive_mode"] else 2
        sorted_items = sorted(enriched, key=lambda x: x["proposing_agent_trust_score"], reverse=True)
        for item in sorted_items:
            c = StockSignal(**item["signal"])
            if c.symbol in seen:
                continue
            seen.add(c.symbol)
            rr = (c.target_price - c.entry_price) / max(c.entry_price - c.stop_loss, 0.01)
            safe_to_buy = (not item["earnings_within_48h"]) and rr >= 1.6 and c.rsi_14 < 75 and len(fallback_trades) < max_buys
            action = "BUY" if safe_to_buy else "SKIP"
            fallback_trades.append(
                FinalDecision(
                    symbol=c.symbol,
                    source_agent=c.agent_id,
                    risk_tier=tier_map[c.agent_id],
                    action=action,
                    conviction=76 if action == "BUY" else 45,
                    entry_price=c.entry_price,
                    stop_loss=c.stop_loss,
                    target_price=c.target_price,
                    news_status="CLEAR" if not item["earnings_within_48h"] else "NEGATIVE_RISK",
                    reasoning=f"[{c.agent_id}] Quant + Trust audit: R:R = 1:{rr:.2f}, RSI = {c.rsi_14}, NIFTY = {regime['nifty_chg_pct']:+.2f}%.",
                )
            )
        decision = CommitteeOutput(selected_trades=fallback_trades)

    buys = [d for d in decision.selected_trades if d.action == "BUY"]
    log_msg(
        sh, cycle_id, "AG4_CHIEF_ANALYST", "AG5_EXECUTION_BROKER", "VERDICT",
        f"Approved {len(buys)} BUY order(s) out of {len(candidates)} proposals (NIFTY {regime['nifty_chg_pct']:+.2f}%).",
        decision.model_dump(),
    )
    return decision


# ==============================================================================
# PHASE 1: FULL 5-AGENT SWING CYCLE (V3.1: 15 SLOTS + ₹25K MIN ORDER + AG3 GATE)
# ==============================================================================
async def run_full_cycle(api_key: str, gcp_creds_dict: dict):
    sh = get_gsheet_client(gcp_creds_dict, SWING_SHEET_NAME)
    acct_ws = sh.worksheet("account")
    cycle_id = now_ist().strftime("CYC-%H%M%S-IST")

    # Always run immediate watchdog check (trailing ladder + dead-money time-stop)
    run_single_watchdog_tick(sh, cycle_id)

    if not acquire_cycle_lock(acct_ws, cooldown_sec=75):
        return

    client = genai.Client(api_key=api_key.strip())
    existing_trades = sh.worksheet("trades").get_all_records()
    open_holdings = {r["symbol"] for r in existing_trades if r.get("status") == "OPEN"}

    if len(open_holdings) >= MAX_OPEN_POSITIONS:
        log_msg(
            sh, cycle_id, "AG5_EXECUTION_BROKER", "SYSTEM", "SLOT_CAP_ACTIVE",
            f"Holding {len(open_holdings)}/{MAX_OPEN_POSITIONS} open swing positions — pausing new entries until a slot frees up.",
            {"open_count": len(open_holdings), "max_allowed": MAX_OPEN_POSITIONS}
        )
        return

    cash = float(acct_ws.acell("A2").value or 1_000_000.0)
    if cash < MIN_ORDER_VALUE:
        log_msg(
            sh, cycle_id, "AG5_EXECUTION_BROKER", "SYSTEM", "MIN_CASH_WAIT",
            f"Available cash ₹{cash:,.2f} below ₹{MIN_ORDER_VALUE:,.0f} minimum order threshold — waiting for exits.",
            {"cash": cash, "min_order_value": MIN_ORDER_VALUE}
        )
        return

    trust_scores = compute_agent_trust_scores(existing_trades)
    lessons = get_recent_agent_lessons(sh, limit=5)
    regime = get_market_regime_and_sectors()
    memory_scorecard = build_trade_memory_scorecard(trust_scores, lessons)
    market_data, live_prices, mover_count = fetch_nse_metrics(excluded_symbols=open_holdings)
    scanned_count = len(live_prices)

    ag1_pool = [d for d in market_data if d["beta"] <= 1.05 and d["ltp"] >= d["sma_200"] and d["rsi_14"] <= 72]
    ag1_slice = enrich_with_intraday_vwap(sorted(ag1_pool or market_data, key=lambda x: (x["beta"], -x["rvol"]))[:12])

    ag2_pool = [d for d in market_data if 0.85 <= d["beta"] <= 1.55 and 45 <= d["rsi_14"] <= 68]
    ag2_slice = enrich_with_intraday_vwap(sorted(ag2_pool or market_data, key=lambda x: x["rvol"], reverse=True)[:12])

    # V3.1 AG3 Regime Gate: HIGH risk only fires when NIFTY >= 20-SMA and at least 2 sectors are green
    ag3_allowed = regime.get("nifty_above_20sma", True) and regime.get("positive_sector_count", 0) >= 2
    if ag3_allowed:
        ag3_pool = [d for d in market_data if d["beta"] >= 1.25 or abs(d["day_change_pct"]) >= 1.5]
        ag3_slice = enrich_with_intraday_vwap(sorted(ag3_pool or market_data, key=lambda x: (abs(x["day_change_pct"]) * x["rvol"]), reverse=True)[:12])
    else:
        ag3_slice = []

    ag1 = await run_screener(
        sh, client, cycle_id, "AG1_LOW_RISK",
        "Focus on LOW-RISK large-cap stability (lowest beta, price above 200-SMA, RSI under 72, tight 1.5x ATR stop).",
        ag1_slice, scanned_count, mover_count, regime, trust_scores["AG1_LOW_RISK"], lessons
    )
    ag2 = await run_screener(
        sh, client, cycle_id, "AG2_MID_RISK",
        "Focus on MID-RISK high-profit momentum swings (strong RVOL, healthy RSI 45-68, 2x ATR stop).",
        ag2_slice, scanned_count, mover_count, regime, trust_scores["AG2_MID_RISK"], lessons
    )
    ag3 = await run_screener(
        sh, client, cycle_id, "AG3_HIGH_RISK",
        "Focus on HIGH-RISK high-profit volatile movers (highest beta or sharp daily % move with volume, tight 1x ATR stop).",
        ag3_slice, scanned_count, mover_count, regime, trust_scores["AG3_HIGH_RISK"], lessons
    )

    candidates = ag1 + ag2 + ag3
    decisions = (await run_ag4(sh, client, cycle_id, candidates, regime, memory_scorecard, trust_scores)).selected_trades if candidates else []

    trades_ws = sh.worksheet("trades")
    cash = float(acct_ws.acell("A2").value or 1_000_000.0)

    for d in decisions:
        if len(open_holdings) >= MAX_OPEN_POSITIONS:
            break
        if d.action != "BUY" or d.conviction < 65 or d.symbol in open_holdings:
            continue
        risk_per_share = max(d.entry_price - d.stop_loss, d.entry_price * 0.005)
        qty_by_risk = int((cash * RISK_PER_TIER[d.risk_tier]) // risk_per_share)
        max_qty_by_cash = int((cash * 0.25) // d.entry_price)
        qty = min(qty_by_risk, max_qty_by_cash)
        cost = qty * d.entry_price

        # V3.1 Minimum Order Size Guardrail (skip micro-lots < ₹25,000)
        if qty > 0 and cost < MIN_ORDER_VALUE:
            log_msg(
                sh, cycle_id, "AG5_EXECUTION_BROKER", "SYSTEM", "MIN_ORDER_SKIP",
                f"Skipped micro-lot {d.symbol} (₹{cost:,.0f} < ₹{MIN_ORDER_VALUE:,.0f} min order size).",
                {"symbol": d.symbol, "cost": round(cost, 2)}
            )
            continue

        if qty > 0 and cost <= cash:
            cash -= cost
            open_holdings.add(d.symbol)
            tagged_reason = f"[{d.source_agent}] {d.reasoning}" if f"[{d.source_agent}]" not in d.reasoning else d.reasoning
            trades_ws.append_row([
                now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                d.symbol, d.risk_tier, "OPEN", qty, d.entry_price, d.stop_loss, d.target_price, "", 0, tagged_reason
            ])
            log_msg(
                sh, cycle_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "PAPER_BUY",
                f"BOUGHT {qty} {d.symbol} ({d.risk_tier} via {d.source_agent}) @ ₹{d.entry_price} | SL: ₹{d.stop_loss} | TP: ₹{d.target_price}",
                d.model_dump()
            )

    acct_ws.update("A2", [[round(cash, 2)]])


# ==============================================================================
# PHASE 2: 30s WATCHDOG + 25%/10% LADDER + 7-DAY TIME-STOP + PERSISTENT FLOOR
# ==============================================================================
def fetch_1m_candles_for_open(symbols: list[str]) -> dict[str, dict]:
    quotes = {}
    if not symbols:
        return quotes
    try:
        data = yf.download(symbols, period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
        for sym in symbols:
            try:
                sub = data[sym].dropna() if len(symbols) > 1 else data.dropna()
                if not sub.empty:
                    last_bar = sub.iloc[-1]
                    quotes[sym] = {
                        "close": round(float(last_bar["Close"]), 2),
                        "high": round(float(last_bar["High"]), 2),
                        "low": round(float(last_bar["Low"]), 2),
                    }
            except Exception:
                continue
    except Exception:
        pass
    return quotes


def compute_25_plus_10_ladder_sl(entry: float, target_dist: float, progress: float) -> tuple[float | None, str]:
    if progress < 0.25:
        return None, ""

    if progress >= 0.95:
        lock_pct = 0.80
        label = "95% Target -> Locked 80% Gain"
    elif progress >= 0.85:
        lock_pct = 0.65
        label = "85% Target -> Locked 65% Gain"
    else:
        step_idx = int((progress - 0.25 + 1e-9) // 0.10)
        step_idx = min(step_idx, 5)
        if step_idx == 0:
            return round(entry * 1.002, 2), "25% Target -> Breakeven + Charges"
        lock_pct = round(step_idx * 0.10, 2)
        milestone_pct = 25 + (step_idx * 10)
        label = f"{milestone_pct}% Target -> Locked {int(lock_pct * 100)}% Gain"

    candidate_sl = round(entry + (lock_pct * target_dist), 2)
    return candidate_sl, label


def get_portfolio_profit_floor(peak_pnl: float) -> float | None:
    if peak_pnl >= 20_000:
        return round(peak_pnl * 0.75, 2)
    elif peak_pnl >= 15_000:
        return 10_500.0
    elif peak_pnl >= 10_000:
        return 6_500.0
    elif peak_pnl >= 7_000:
        return 3_500.0
    return None


def trading_days_held(opened_ts_str: str) -> int:
    try:
        clean = str(opened_ts_str).replace(" IST", "").strip()
        opened_dt = pd.to_datetime(clean)
        cur_dt = now_ist().replace(tzinfo=None)
        if cur_dt <= opened_dt:
            return 0
        bdays = len(pd.bdate_range(opened_dt.date(), cur_dt.date())) - 1
        return max(0, bdays)
    except Exception:
        return 0


def run_single_watchdog_tick(sh, tick_id: str, state: dict | None = None):
    acct_ws = sh.worksheet("account")
    if state is None:
        state = {"peak_portfolio_pnl": get_persisted_peak_pnl(acct_ws)}

    trades_ws = sh.worksheet("trades")
    records = trades_ws.get_all_records()

    open_rows = [(idx, row) for idx, row in enumerate(records, start=2) if row.get("status") == "OPEN"]
    if not open_rows:
        return

    open_syms = list({row["symbol"] for _, row in open_rows})
    quotes = fetch_1m_candles_for_open(open_syms)
    if not quotes:
        return

    cash = float(acct_ws.acell("A2").value or 1_000_000.0)
    cash_changed = False
    remaining_open = []
    combined_unrealized = 0.0
    closed_events = []

    for idx, row in open_rows:
        sym = row["symbol"]
        q = quotes.get(sym)
        if not q:
            continue

        ltp, bar_high, bar_low = q["close"], q["high"], q["low"]
        entry = float(row["entry"])
        sl = float(row["sl"])
        target = float(row["target"])
        qty = int(row["qty"])
        target_dist = max(target - entry, entry * 0.01)

        # 1. STOP-LOSS CHECK
        if bar_low <= sl or ltp <= sl:
            exit_px = round(min(ltp, sl) if bar_low <= sl else ltp, 2)
            pnl = round((exit_px - entry) * qty, 2)
            cash += exit_px * qty
            cash_changed = True
            exit_tag = "TRAILING_SL_HIT" if sl >= entry else "SL_HIT"
            trades_ws.update(f"D{idx}", [[exit_tag]])
            trades_ws.update(f"I{idx}:J{idx}", [[exit_px, pnl]])
            log_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", exit_tag,
                f"SOLD {qty} {sym} ({exit_tag}) @ ₹{exit_px} | Net P&L: ₹{pnl:+,.2f}",
                {"symbol": sym, "exit_price": exit_px, "pnl": pnl}
            )
            closed_events.append((row, exit_tag, pnl, entry, exit_px))
            continue

        # 2. TARGET ROLLOVER
        if bar_high >= target or ltp >= target:
            new_sl = round(target * 0.9975, 2)
            step_extension = round(target_dist * 0.5, 2)
            new_target = round(target + step_extension, 2)
            if new_sl > sl:
                trades_ws.update(f"G{idx}:H{idx}", [[new_sl, new_target]])
                sl, target = new_sl, new_target
                log_msg(
                    sh, tick_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "TARGET_ROLLOVER",
                    f"🚀 {sym} hit Target! Rolled SL up to ₹{new_sl} (0.25% buffer) & extended Target 2 to ₹{new_target}",
                    {"symbol": sym, "new_sl": new_sl, "new_target": new_target}
                )
        # 3. 25% BREAKEVEN + 10% INCREMENTAL STEP-TRAILING LADDER
        else:
            progress = (bar_high - entry) / target_dist
            ladder_sl, ladder_label = compute_25_plus_10_ladder_sl(entry, target_dist, progress)
            if ladder_sl is not None and ladder_sl > sl:
                trades_ws.update(f"G{idx}", [[ladder_sl]])
                sl = ladder_sl
                log_msg(
                    sh, tick_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "TRAILING_SL_LADDER",
                    f"🛡️ {sym} reached {ladder_label} (SL raised to ₹{ladder_sl})",
                    {"symbol": sym, "new_sl": ladder_sl, "progress_pct": round(progress * 100, 1)}
                )

        # 4. V3.1 7-TRADING-DAY DEAD-MONEY TIME-STOP
        days_open = trading_days_held(row.get("timestamp", ""))
        ret_pct_now = ((ltp - entry) / max(entry, 0.01)) * 100.0
        if days_open >= DEAD_MONEY_MAX_DAYS and ret_pct_now < 1.0 and sl < entry:
            pnl = round((ltp - entry) * qty, 2)
            cash += ltp * qty
            cash_changed = True
            trades_ws.update(f"D{idx}", [["TIME_STOP_EXIT"]])
            trades_ws.update(f"I{idx}:J{idx}", [[ltp, pnl]])
            log_msg(
                sh, tick_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "TIME_STOP_EXIT",
                f"⏳ Dead-Money Time-Stop ({days_open}d held, {ret_pct_now:+.2f}%): SOLD {qty} {sym} @ ₹{ltp} | Net P&L: ₹{pnl:+,.2f}",
                {"symbol": sym, "days_open": days_open, "exit_price": ltp, "pnl": pnl}
            )
            closed_events.append((row, "TIME_STOP_EXIT", pnl, entry, ltp))
            continue

        unrealized = (ltp - entry) * qty
        combined_unrealized += unrealized
        remaining_open.append((idx, row, sym, qty, entry, ltp))

    # 5. PERSISTENT STEPPED PORTFOLIO PROFIT FLOOR
    if combined_unrealized > state["peak_portfolio_pnl"]:
        state["peak_portfolio_pnl"] = round(combined_unrealized, 2)
        set_persisted_peak_pnl(acct_ws, state["peak_portfolio_pnl"])

    floor = get_portfolio_profit_floor(state["peak_portfolio_pnl"])
    if floor is not None and remaining_open and combined_unrealized <= floor:
        total_locked_pnl = 0.0
        for idx, row, sym, qty, entry, ltp in remaining_open:
            pnl = round((ltp - entry) * qty, 2)
            total_locked_pnl += pnl
            cash += ltp * qty
            cash_changed = True
            trades_ws.update(f"D{idx}", [["PORTFOLIO_FLOOR_EXIT"]])
            trades_ws.update(f"I{idx}:J{idx}", [[ltp, pnl]])
            closed_events.append((row, "PORTFOLIO_FLOOR_EXIT", pnl, entry, ltp))

        log_msg(
            sh, tick_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "PORTFOLIO_FLOOR_EXIT",
            f"💰 Portfolio Profit Floor triggered (Peak: +₹{state['peak_portfolio_pnl']:,.2f}, Floor: +₹{floor:,.2f}) -> Liquidated {len(remaining_open)} positions | Locked Net P&L: ₹{total_locked_pnl:+,.2f}",
            {"peak_pnl": state["peak_portfolio_pnl"], "floor": floor, "realized_pnl": round(total_locked_pnl, 2)}
        )
        state["peak_portfolio_pnl"] = 0.0
        set_persisted_peak_pnl(acct_ws, 0.0)

    if cash_changed:
        acct_ws.update("A2", [[round(cash, 2)]])

    if closed_events:
        old_scores = compute_agent_trust_scores(records)
        updated_records = trades_ws.get_all_records()
        new_scores = compute_agent_trust_scores(updated_records)
        for row, exit_tag, pnl, entry, exit_px in closed_events:
            ag = infer_trade_agent(row)
            record_trade_reflection(
                sh, tick_id, ag, row["symbol"], str(row.get("tier", "MID")),
                exit_tag, pnl, entry, exit_px,
                old_scores.get(ag, {}).get("score", 50),
                new_scores.get(ag, {}).get("score", 50),
            )


# ==============================================================================
# PHASE 3: 3:45 PM IST EOD ATR RECALIBRATOR + DAILY PEAK RESET
# ==============================================================================
def run_eod_sl_recalibration(gcp_creds_dict: dict):
    if now_ist().weekday() >= 5:
        return

    sh = get_gsheet_client(gcp_creds_dict, SWING_SHEET_NAME)
    eod_id = now_ist().strftime("EOD-%Y%m%d-IST")
    trades_ws = sh.worksheet("trades")
    acct_ws = sh.worksheet("account")
    records = trades_ws.get_all_records()

    # Reset daily peak P&L tracker at EOD so next trading day starts fresh
    set_persisted_peak_pnl(acct_ws, 0.0)

    open_rows = [(idx, row) for idx, row in enumerate(records, start=2) if row.get("status") == "OPEN"]
    if not open_rows:
        return

    regime = get_market_regime_and_sectors()
    regime_tightener = 0.25 if regime.get("defensive_mode") else 0.0

    open_syms = list({row["symbol"] for _, row in open_rows})
    try:
        data = yf.download(open_syms, period="2mo", interval="1d", group_by="ticker", progress=False, threads=True)
    except Exception:
        return

    ratcheted_count = 0
    for idx, row in open_rows:
        sym = row["symbol"]
        tier = str(row.get("tier", "MID")).upper()
        curr_sl = float(row["sl"])
        try:
            sub = data[sym].dropna(subset=["Close", "High", "Low"]) if len(open_syms) > 1 else data.dropna(subset=["Close", "High", "Low"])
            if len(sub) < 15:
                continue

            daily_close = float(sub["Close"].iloc[-1])
            atr_14 = float((sub["High"] - sub["Low"]).rolling(14).mean().iloc[-1])
            mult = max(0.75, ATR_MULT_PER_TIER.get(tier, 1.5) - regime_tightener)

            raw_chandelier_sl = daily_close - (mult * atr_14)
            max_allowed_eod_sl = daily_close * 0.992
            new_sl = round(min(raw_chandelier_sl, max_allowed_eod_sl), 2)

            if new_sl > curr_sl:
                trades_ws.update(f"G{idx}", [[new_sl]])
                ratcheted_count += 1
                log_msg(
                    sh, eod_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "EOD_SL_RATCHET",
                    f"📐 EOD ATR Recalibration: {sym} ({tier}) SL raised ₹{curr_sl} -> ₹{new_sl} (Close ₹{daily_close:.2f}, ATR ₹{atr_14:.2f})",
                    {"symbol": sym, "old_sl": curr_sl, "new_sl": new_sl, "daily_close": round(daily_close, 2), "atr_14": round(atr_14, 2)}
                )
        except Exception:
            continue

    if ratcheted_count == 0:
        log_msg(
            sh, eod_id, "AG5_EXECUTION_BROKER", "SYSTEM", "EOD_SL_CHECK",
            f"EOD ATR Recalibration complete across {len(open_rows)} open positions — existing trailing SLs already optimal.",
            {"open_count": len(open_rows), "defensive_mode": regime.get("defensive_mode", False)}
        )


async def run_split_speed_session(api_key: str, gcp_creds_dict: dict, watchdog_ticks: int = 27, interval_sec: int = 30):
    await run_full_cycle(api_key, gcp_creds_dict)

    sh = get_gsheet_client(gcp_creds_dict, SWING_SHEET_NAME)
    watchdog_state = {"peak_portfolio_pnl": get_persisted_peak_pnl(sh.worksheet("account"))}

    for tick in range(1, watchdog_ticks + 1):
        if not is_nse_open_now():
            break
        time.sleep(interval_sec)
        try:
            tick_id = now_ist().strftime(f"WD-{tick:02d}-%H%M%S")
            run_single_watchdog_tick(sh, tick_id, watchdog_state)
        except Exception:
            continue
