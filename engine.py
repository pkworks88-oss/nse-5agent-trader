import os
import json
import asyncio
import sqlite3
from datetime import datetime
from urllib.parse import quote
import yfinance as yf
import feedparser
from google import genai
from google.genai import types
from google.genai.errors import APIError
from schemas import StockSignal, CommitteeOutput, FinalDecision

DB_PATH = "nse_demo_trades.db"
CANDIDATE_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

NSE_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "SUNPHARMA.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "ADANIENT.NS", "BAJFINANCE.NS",
    "TRENT.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "DIXON.NS", "KPITTECH.NS"
]

RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}


def init_db(initial_capital: float = 1_000_000.0):
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()


def log_msg(cycle_id: str, sender: str, recipient: str, msg_type: str, summary: str, payload):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO agent_messages (cycle_id, timestamp, sender, recipient, msg_type, summary, payload_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            cycle_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            sender,
            recipient,
            msg_type,
            summary,
            json.dumps(payload, indent=2),
        ),
    )
    conn.commit()
    conn.close()


def fetch_nse_metrics() -> list[dict]:
    """Batch-downloads all NSE tickers in a single fast request (~2 seconds)."""
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

            # Estimate volatility proxy from ATR % instead of slow per-ticker .info HTTP calls
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
    """Hard 12-second timeout per LLM call so the UI never hangs."""
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
        log_msg(cycle_id, agent_id, "AG4_CHIEF_ANALYST", "NO_SIGNAL", "No NSE market data fetched this cycle.", [])
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
    log_msg(cycle_id, agent_id, "AG4_CHIEF_ANALYST", "CANDIDATE_PROPOSAL", summary, [s.model_dump() for s in signals])
    return signals


async def run_ag4(client, cycle_id: str, candidates: list[StockSignal]) -> CommitteeOutput:
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
        cycle_id,
        "AG4_CHIEF_ANALYST",
        "AG5_EXECUTION_BROKER",
        "VERDICT",
        f"Approved {len(buys)} BUY order(s) out of {len(candidates)} proposals.",
        decision.model_dump(),
    )
    return decision


def ag5_broker_step(cycle_id: str, decisions: list[FinalDecision], live_prices: dict[str, float]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cash = cur.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]

    for tid, sym, qty, entry, sl, target in cur.execute(
        "SELECT id, symbol, qty, entry, sl, target FROM trades WHERE status='OPEN'"
    ).fetchall():
        ltp = live_prices.get(sym)
        if ltp and (ltp <= sl or ltp >= target):
            outcome = "TARGET_HIT" if ltp >= target else "SL_HIT"
            pnl = round((ltp - entry) * qty, 2)
            cash += ltp * qty
            cur.execute("UPDATE account SET cash=?", (cash,))
            cur.execute("UPDATE trades SET status=?, exit_price=?, pnl=? WHERE id=?", (outcome, ltp, pnl, tid))
            log_msg(
                cycle_id,
                "AG5_EXECUTION_BROKER",
                "PORTFOLIO_LEDGER",
                outcome,
                f"SOLD {sym} ({outcome}) @ ₹{ltp} | Net P&L: ₹{pnl}",
                {"symbol": sym, "pnl": pnl},
            )

    for d in decisions:
        if d.action != "BUY" or d.conviction < 65:
            continue
        if cur.execute("SELECT id FROM trades WHERE symbol=? AND status='OPEN'", (d.symbol,)).fetchone():
            continue
        risk_per_share = max(d.entry_price - d.stop_loss, d.entry_price * 0.005)
        qty = int((cash * RISK_PER_TIER[d.risk_tier]) // risk_per_share)
        cost = qty * d.entry_price
        if qty > 0 and cost <= cash * 0.25:
            cash -= cost
            cur.execute("UPDATE account SET cash=?", (cash,))
            cur.execute(
                """INSERT INTO trades (timestamp, symbol, tier, status, qty, entry, sl, target, reason)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    d.symbol,
                    d.risk_tier,
                    "OPEN",
                    qty,
                    d.entry_price,
                    d.stop_loss,
                    d.target_price,
                    d.reasoning,
                ),
            )
            log_msg(
                cycle_id,
                "AG5_EXECUTION_BROKER",
                "PORTFOLIO_LEDGER",
                "PAPER_BUY",
                f"BOUGHT {qty} {d.symbol} ({d.risk_tier}) @ ₹{d.entry_price} | SL: ₹{d.stop_loss} | TP: ₹{d.target_price}",
                d.model_dump(),
            )

    conn.commit()
    conn.close()


async def run_full_cycle(api_key: str):
    client = genai.Client(api_key=api_key.strip())
    cycle_id = datetime.now().strftime("CYC-%H%M%S")
    market_data = fetch_nse_metrics()
    live_prices = {m["symbol"]: m["ltp"] for m in market_data}

    ag1_slice = [d for d in market_data if d["beta"] <= 1.05] or market_data
    ag2_slice = [d for d in market_data if 0.85 <= d["beta"] <= 1.55] or market_data
    ag3_slice = [d for d in market_data if d["beta"] >= 1.15 or abs(d["day_change_pct"]) >= 1.2] or market_data

    ag1 = await run_screener(
        client, cycle_id, "AG1_LOW_RISK",
        "Focus on LOW-RISK large-cap stability (lowest beta, price near/above 200-SMA, tight 1.5x ATR stop).",
        ag1_slice,
    )
    ag2 = await run_screener(
        client, cycle_id, "AG2_MID_RISK",
        "Focus on MID-RISK high-profit momentum swings (strong RVOL, healthy RSI 45-68, 2x ATR stop).",
        ag2_slice,
    )
    ag3 = await run_screener(
        client, cycle_id, "AG3_HIGH_RISK",
        "Focus on HIGH-RISK high-profit volatile movers (highest beta or sharp daily % move, tight 1x ATR stop).",
        ag3_slice,
    )

    candidates = ag1 + ag2 + ag3
    decisions = (await run_ag4(client, cycle_id, candidates)).selected_trades if candidates else []
    ag5_broker_step(cycle_id, decisions, live_prices)
