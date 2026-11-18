import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
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

NSE_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "SUNPHARMA.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "ADANIENT.NS", "BAJFINANCE.NS",
    "TRENT.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "DIXON.NS", "KPITTECH.NS"
]

RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def get_gsheet_client(gcp_creds_dict: dict):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(gcp_creds_dict, scopes=scopes)
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


def fetch_nse_metrics() -> list[dict]:
    snapshots = []
    try:
        data = yf.download(NSE_WATCHLIST, period="6mo", interval="1d", group_by="ticker", progress=False, threads=True)
    except Exception:
        return snapshots

    for sym in NSE_WATCHLIST:
        try:
            hist = data[sym].dropna(subset=["Close"])
            if len(hist) < 30:
                continue
            close, volume = hist["Close"], hist["Volume"]
            ltp = float(close.iloc[-1])
            sma_50 = float(close.rolling(min(50, len(hist))).mean().iloc[-1])
            sma_200 = float(close.rolling(min(200, len(hist))).mean().iloc[-1])

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, float("nan"))
            rsi_val = (100 - (100 / (1 + rs))).iloc[-1]
            rsi = float(rsi_val) if rsi_val == rsi_val else 50.0

            atr = float((hist["High"] - hist["Low"]).rolling(14).mean().iloc[-1])
            avg_vol = float(volume.rolling(20).mean().iloc[-1])
            rvol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

            atr_pct = (atr / ltp) * 100
            beta_proxy = round(max(0.6, min(2.0, atr_pct / 1.8)), 2)

            snapshots.append({
                "symbol": sym,
                "ltp": round(ltp, 2),
                "sma_50": round(sma_50, 2),
                "sma_200": round(sma_200, 2),
                "rsi_14": round(rsi, 2),
                "atr_14": round(atr, 2),
                "rvol": round(rvol, 2),
                "beta": beta_proxy,
                "day_change_pct": round(((ltp - float(close.iloc[-2])) / float(close.iloc[-2])) * 100, 2),
            })
        except Exception:
            continue
    return snapshots


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
            resp = await asyncio.wait_for(coro, timeout=12.0)
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
                setup_reason=f"Quant engine: RVOL {d['rvol']}x, RSI {d['rsi_14']}, ATR 1:2 R:R setup.",
            )
        )
    return signals


async def run_screener(sh, client, cycle_id: str, agent_id: str, rules: str, data_slice: list[dict]) -> list[StockSignal]:
    if not data_slice:
        log_msg(sh, cycle_id, agent_id, "AG4_CHIEF_ANALYST", "NO_SIGNAL", "No NSE market data fetched.", [])
        return []

    prompt = (
        f"You are {agent_id} for NSE India. {rules} "
        f"From this NSE data, pick the 2 best stocks matching your risk tier with realistic INR "
        f"entry_price, stop_loss, and target_price: {json.dumps(data_slice)}"
    )
    try:
        raw = await generate_fast(client, prompt, list[StockSignal], temperature=0.2)
        signals = [StockSignal(**item) for item in raw]
        mode_tag = ""
    except Exception:
        signals = quant_fallback_screener(agent_id, data_slice)
        mode_tag = " [Quant Engine]"

    summary = (
        f"Sent {len(signals)} stock(s) to AG4{mode_tag}: "
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
        "with their news headlines. Mark each BUY or SKIP; approve 1-3 top setups with clean news and R:R >= 1:1.6. "
        f"Inputs: {json.dumps(enriched)}"
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
            action = "BUY" if rr >= 1.6 and len(fallback_trades) < 2 else "SKIP"
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

    # 2. Execute new virtual BUY orders
    for d in decisions:
        if d.action != "BUY" or d.conviction < 65 or d.symbol in open_symbols:
            continue
        risk_per_share = max(d.entry_price - d.stop_loss, d.entry_price * 0.005)
        qty = int((cash * RISK_PER_TIER[d.risk_tier]) // risk_per_share)
        cost = qty * d.entry_price
        if qty > 0 and cost <= cash * 0.25:
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
    market_data = fetch_nse_metrics()
    live_prices = {m["symbol"]: m["ltp"] for m in market_data}

    ag1_slice = [d for d in market_data if d["beta"] <= 1.05] or market_data
    ag2_slice = [d for d in market_data if 0.85 <= d["beta"] <= 1.55] or market_data
    ag3_slice = [d for d in market_data if d["beta"] >= 1.15 or abs(d["day_change_pct"]) >= 1.2] or market_data

    ag1 = await run_screener(
        sh, client, cycle_id, "AG1_LOW_RISK",
        "Focus on LOW-RISK large-cap stability (lowest beta, price near/above 200-SMA, tight 1.5x ATR stop).",
        ag1_slice,
    )
    ag2 = await run_screener(
        sh, client, cycle_id, "AG2_MID_RISK",
        "Focus on MID-RISK high-profit momentum swings (strong RVOL, healthy RSI 45-68, 2x ATR stop).",
        ag2_slice,
    )
    ag3 = await run_screener(
        sh, client, cycle_id, "AG3_HIGH_RISK",
        "Focus on HIGH-RISK high-profit volatile movers (highest beta or sharp daily % move, tight 1x ATR stop).",
        ag3_slice,
    )

    candidates = ag1 + ag2 + ag3
    decisions = (await run_ag4(sh, client, cycle_id, candidates)).selected_trades if candidates else []
    ag5_broker_step(sh, cycle_id, decisions, live_prices)
