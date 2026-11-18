import os
import io
import json
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
CANDIDATE_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
SHEET_NAME = "NSE_5Agent_Ledger"

# Backup liquid NSE 250 pool (used only if NSE live endpoints throttle cloud IPs)
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

RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def get_gsheet_client(gcp_creds_dict: dict):
    creds_copy = dict(gcp_creds_dict)
    if "private_key" in creds_copy:
        creds_copy["private_key"] = creds_copy["private_key"].replace("\\n", "\n")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_copy, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME)


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


def fetch_nse_live_movers() -> list[str]:
    """Pulls live intraday NSE Top Gainers, Top Losers, and Volume Spurts across all 2,200+ NSE stocks."""
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
        # Initialize NSE session cookie
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
                # Handle both variations schema (allSec.data) and volume-gainers schema (data)
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
    """Combines live NSE Top Gainers/Losers/Volume Spurts + NIFTY 200 index constituents (~250 unique stocks)."""
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

    # Put live intraday movers first so they are guaranteed inclusion in the 250-stock batch
    combined = list(dict.fromkeys(live_movers + nifty200_symbols + FALLBACK_NSE_250))
    return combined[:250], len(live_movers)


def fetch_nse_metrics(excluded_symbols: set[str] | None = None) -> tuple[list[dict], dict[str, float], int]:
    """Scans ~250 dynamic NSE stocks in a single batch call and returns filtered candidates + live prices."""
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
                setup_reason=f"Quant funnel: RVOL {d['rvol']}x, RSI {d['rsi_14']}, ATR 1:2 R:R setup.",
            )
        )
    return signals


async def run_screener(sh, client, cycle_id: str, agent_id: str, rules: str, data_slice: list[dict], scanned_count: int, mover_count: int) -> list[StockSignal]:
    if not data_slice:
        log_msg(sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "NO_SIGNAL", "No NSE market data fetched.", [])
        return []

    prompt = (
        f"You are {agent_id} for NSE India (scanning {scanned_count} live NSE stocks including {mover_count} live intraday movers). {rules} "
        f"From these top-ranked quantitative candidates, pick the 2 best distinct stocks matching your risk tier "
        f"with realistic INR entry_price, stop_loss, and target_price: {json.dumps(data_slice)}"
    )
    try:
        raw = await generate_fast(client, prompt, list[StockSignal], temperature=0.2)
        signals = [StockSignal(**item) for item in raw]
        mode_tag = ""
    except Exception:
        signals = quant_fallback_screener(agent_id, data_slice)
        mode_tag = " [Quant Engine]"

    mover_note = f" ({mover_count} live movers)" if mover_count > 0 else ""
    summary = (
        f"Scanned {scanned_count} NSE stocks{mover_note} → Sent {len(signals)} to AG4{mode_tag}: "
        + ", ".join([f"{s.symbol} @ ₹{s.entry_price}" for s in signals])
        if signals
        else "No setups approved."
    )
    log_msg(sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "CANDIDATE_PROPOSAL", summary, [s.model_dump() for s in signals])
    return signals


async def run_ag4(sh, client, cycle_id: str, candidates: list[StockSignal]) -> CommitteeOutput:
    enriched = [{"signal": c.model_dump(), "news": fetch_nse_news(c.symbol)} for c in candidates]
    prompt = (
        "You are AG4, Chief Analyst for NSE India. Audit all candidates from AG1 (Low), AG2 (Mid), and AG3 (High) "
        "with their news headlines. Mark each BUY or SKIP; approve 1-3 top setups with clean news, non-overbought RSI, "
        f"and R:R >= 1:1.6. Inputs: {json.dumps(enriched)}"
    )
    try:
        raw = await generate_fast(client, prompt, CommitteeOutput, temperature=0.1)
        decision = CommitteeOutput(**raw)
    except Exception:
        tier_map = {"AG1_LOW_RISK": "LOW", "AG2_MID_RISK": "MID", "AG3_HIGH_RISK": "HIGH"}
        fallback_trades, seen = [], set()
        for c in candidates:
            if c.symbol in seen:
                continue
            seen.add(c.symbol)
            rr = (c.target_price - c.entry_price) / max(c.entry_price - c.stop_loss, 0.01)
            action = "BUY" if rr >= 1.6 and c.rsi_14 < 75 and len(fallback_trades) < 2 else "SKIP"
            fallback_trades.append(
                FinalDecision(
                    symbol=c.symbol,
                    risk_tier=tier_map[c.agent_id],
                    action=action,
                    conviction=76 if action == "BUY" else 50,
                    entry_price=c.entry_price,
                    stop_loss=c.stop_loss,
                    target_price=c.target_price,
                    news_status="CLEAR",
                    reasoning=f"Quant audit: R:R = 1:{rr:.2f}, RSI = {c.rsi_14}, RVOL = {c.rvol}x.",
                )
            )
        decision = CommitteeOutput(selected_trades=fallback_trades)

    buys = [d for d in decision.selected_trades if d.action == "BUY"]
    log_msg(
        sh,
        cycle_id,
        "AG4_CHIEF_ANALYST",
        "AG5_EXECUTION_BROKER",
        "VERDICT",
        f"Approved {len(buys)} BUY order(s) out of {len(candidates)} proposals.",
        decision.model_dump(),
    )
    return decision


def ag5_broker_step(sh, cycle_id: str, decisions: list[FinalDecision], live_prices: dict[str, float]):
    acct_ws = sh.worksheet("account")
    trades_ws = sh.worksheet("trades")
    cash = float(acct_ws.acell("A2").value or 1_000_000.0)
    records = trades_ws.get_all_records()

    # 1. Monitor open positions for SL/TP exits
    open_symbols = set()
    for idx, row in enumerate(records, start=2):
        if row.get("status") == "OPEN":
            sym = row["symbol"]
            open_symbols.add(sym)
            ltp = live_prices.get(sym)
            sl, target, entry, qty = float(row["sl"]), float(row["target"]), float(row["entry"]), int(row["qty"])
            if ltp and (ltp <= sl or ltp >= target):
                outcome = "TARGET_HIT" if ltp >= target else "SL_HIT"
                pnl = round((ltp - entry) * qty, 2)
                cash += ltp * qty
                trades_ws.update(f"D{idx}", [[outcome]])
                trades_ws.update(f"I{idx}:J{idx}", [[ltp, pnl]])
                open_symbols.discard(sym)
                log_msg(
                    sh, cycle_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", outcome,
                    f"SOLD {sym} ({outcome}) @ ₹{ltp} | Net P&L: ₹{pnl}", {"symbol": sym, "pnl": pnl}
                )

    # 2. Execute new virtual BUY orders (risk-sized and capped at 25% of available cash)
    for d in decisions:
        if d.action != "BUY" or d.conviction < 65 or d.symbol in open_symbols:
            continue
        risk_per_share = max(d.entry_price - d.stop_loss, d.entry_price * 0.005)
        qty_by_risk = int((cash * RISK_PER_TIER[d.risk_tier]) // risk_per_share)
        max_qty_by_cash = int((cash * 0.25) // d.entry_price)
        qty = min(qty_by_risk, max_qty_by_cash)
        cost = qty * d.entry_price
        if qty > 0 and cost <= cash:
            cash -= cost
            open_symbols.add(d.symbol)
            trades_ws.append_row([
                now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                d.symbol, d.risk_tier, "OPEN", qty, d.entry_price, d.stop_loss, d.target_price, "", 0, d.reasoning
            ])
            log_msg(
                sh, cycle_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "PAPER_BUY",
                f"BOUGHT {qty} {d.symbol} ({d.risk_tier}) @ ₹{d.entry_price} | SL: ₹{d.stop_loss} | TP: ₹{d.target_price}",
                d.model_dump()
            )

    acct_ws.update("A2", [[round(cash, 2)]])


async def run_full_cycle(api_key: str, gcp_creds_dict: dict):
    sh = get_gsheet_client(gcp_creds_dict)
    client = genai.Client(api_key=api_key.strip())
    cycle_id = now_ist().strftime("CYC-%H%M%S-IST")

    existing_trades = sh.worksheet("trades").get_all_records()
    open_holdings = {r["symbol"] for r in existing_trades if r.get("status") == "OPEN"}

    market_data, live_prices, mover_count = fetch_nse_metrics(excluded_symbols=open_holdings)
    scanned_count = len(live_prices)

    ag1_pool = [d for d in market_data if d["beta"] <= 1.05 and d["ltp"] >= d["sma_200"] and d["rsi_14"] <= 72]
    ag1_slice = sorted(ag1_pool or market_data, key=lambda x: (x["beta"], -x["rvol"]))[:15]

    ag2_pool = [d for d in market_data if 0.85 <= d["beta"] <= 1.55 and 45 <= d["rsi_14"] <= 68]
    ag2_slice = sorted(ag2_pool or market_data, key=lambda x: x["rvol"], reverse=True)[:15]

    ag3_pool = [d for d in market_data if d["beta"] >= 1.25 or abs(d["day_change_pct"]) >= 1.5]
    ag3_slice = sorted(ag3_pool or market_data, key=lambda x: (abs(x["day_change_pct"]) * x["rvol"]), reverse=True)[:15]

    ag1 = await run_screener(
        sh, client, cycle_id, "AG1_LOW_RISK",
        "Focus on LOW-RISK large-cap stability (lowest beta, price above 200-SMA, RSI under 72, tight 1.5x ATR stop).",
        ag1_slice, scanned_count, mover_count
    )
    ag2 = await run_screener(
        sh, client, cycle_id, "AG2_MID_RISK",
        "Focus on MID-RISK high-profit momentum swings (strong RVOL, healthy RSI 45-68, 2x ATR stop).",
        ag2_slice, scanned_count, mover_count
    )
    ag3 = await run_screener(
        sh, client, cycle_id, "AG3_HIGH_RISK",
        "Focus on HIGH-RISK high-profit volatile movers (highest beta or sharp daily % move with volume, tight 1x ATR stop).",
        ag3_slice, scanned_count, mover_count
    )

    candidates = ag1 + ag2 + ag3
    decisions = (await run_ag4(sh, client, cycle_id, candidates)).selected_trades if candidates else []
    ag5_broker_step(sh, cycle_id, decisions, live_prices)
