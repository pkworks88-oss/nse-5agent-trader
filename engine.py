import os
import json
import asyncio
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
import yfinance as yf
import feedparser
from google import genai
from google.genai import types
from google.genai.errors import APIError
from schemas import StockSignal, CommitteeOutput, FinalDecision

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = "nse_demo_trades.db"
CANDIDATE_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

NSE_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "SUNPHARMA.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "ADANIENT.NS", "BAJFINANCE.NS",
    "TRENT.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "DIXON.NS", "KPITTECH.NS"
]

RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}


def now_ist() -> datetime:
    return datetime.now(IST)


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db(initial_capital: float = 1_000_000.0):
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash REAL)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, symbol TEXT, tier TEXT, status TEXT,
                qty INTEGER, entry REAL, sl REAL, target REAL, exit_price REAL, pnl REAL, reason TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT, timestamp TEXT, sender TEXT, recipient TEXT,
                msg_type TEXT, summary TEXT, payload_json TEXT
            )
        """)
        if conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 0:
            conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (initial_capital,))
        conn.commit()


def log_msg(cycle_id: str, sender: str, recipient: str, msg_type: str, summary: str, payload):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_messages (cycle_id, timestamp, sender, recipient, msg_type, summary, payload_json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                cycle_id,
                now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                sender,
                recipient,
                msg_type,
                summary,
                json.dumps(payload, indent=2),
            ),
        )
        conn.commit()


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


async def run_screener(client, cycle_id: str, agent_id: str, rules: str, data_slice: list[dict]) -> list[StockSignal]:
    if not data_slice:
        log_msg(cycle_id, agent_id, "AG
