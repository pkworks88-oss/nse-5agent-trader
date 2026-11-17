import os, json, asyncio, sqlite3
from datetime import datetime
from urllib.parse import quote
import yfinance as yf
import feedparser
from google import genai
from google.genai import types
from schemas import StockSignal, CommitteeOutput, FinalDecision

DB_PATH = "nse_demo_trades.db"
MODEL = "gemini-2.5-flash"

NSE_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "SUNPHARMA.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "ADANIENT.NS", "BAJFINANCE.NS",
    "TRENT.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "DIXON.NS", "KPITTECH.NS"
]

RISK_PER_TIER = {"LOW": 0.015, "MID": 0.010, "HIGH": 0.004}

def init_db(initial_capital=1_000_000.0):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash REAL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, tier TEXT, status TEXT,
        qty INTEGER, entry REAL, sl REAL, target REAL, exit_price REAL, pnl REAL, reason TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id TEXT, timestamp TEXT, sender TEXT, recipient TEXT,
        msg_type TEXT, summary TEXT, payload_json TEXT
    )""")
    if conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 0:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (initial_capital,))
    conn.commit()
    conn.close()

def log_msg(cycle_id, sender, recipient, msg_type, summary, payload):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO agent_messages (cycle_id, timestamp, sender, recipient, msg_type, summary, payload_json) VALUES (?,?,?,?,?,?,?)",
        (cycle_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sender, recipient, msg_type, summary, json.dumps(payload, indent=2))
    )
    conn.commit()
    conn.close()

def fetch_nse_metrics() -> list[dict]:
    snapshots = []
    for sym in NSE_WATCHLIST:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="6mo", interval="1d")
            if len(hist) < 50:
                continue
            close, volume = hist["Close"], hist["Volume"]
            ltp = float(close.iloc[-1])
            sma_50 = float(close.rolling(50).mean().iloc[-1])
            sma_200 = float(close.rolling(200).mean().iloc[-1]) if len(hist) >= 200 else sma_50

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = float((100 - (100 / (1 + (gain / loss)))).iloc[-1])

            atr = float((hist["High"] - hist["Low"]).rolling(14).mean().iloc[-1])
            rvol = float(volume.iloc[-1] / volume.rolling(20).mean().iloc[-1])
            beta = float(ticker.info.get("beta") or 1.0)

            snapshots.append({
                "symbol": sym, "ltp": round(ltp, 2), "sma_50": round(sma_50, 2),
                "sma_200": round(sma_200, 2), "rsi_14": round(rsi, 2),
                "atr_14": round(atr, 2), "rvol": round(rvol, 2), "beta": round(beta, 2),
                "day_change_pct": round(((ltp - float(close.iloc[-2])) / float(close.iloc[-2])) * 100, 2)
            })
        except Exception:
            continue
    return snapshots

def fetch_nse_news(symbol: str) -> list[str]:
    clean_name = symbol.replace(".NS", "")
    url = f"https://news.google.com/rss/search?q={quote(clean_name + ' NSE stock')}&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(url)
    return [entry.title for entry in feed.entries[:4]]

async def run_screener(client, cycle_id: str, agent_id: str, rules: str, data_slice: list[dict]) -> list[StockSignal]:
    if not data_slice:
        log_msg(cycle_id, agent_id, "AG4_CHIEF_ANALYST", "NO_SIGNAL", "No NSE tickers matched filter criteria.", [])
        return []
    prompt = f"You are {agent_id} for NSE India. {rules} Pick up to 2 best stocks from this real-time data: {json.dumps(data_slice)}"
    resp = await client.aio.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=list[StockSignal], temperature=0.2)
    )
    signals = [StockSignal(**item) for item in json.loads(resp.text)]
    summary = f"Sent {len(signals)} stock(s) to AG4: " + ", ".join([f"{s.symbol} @ ₹{s.entry_price}" for s in signals]) if signals else "No setups approved."
    log_msg(cycle_id, agent_id, "AG4_CHIEF_ANALYST", "CANDIDATE_PROPOSAL", summary, [s.model_dump() for s in signals])
    return signals

async def run_ag4(client, cycle_id: str, candidates: list[StockSignal]) -> CommitteeOutput:
    enriched = [{"signal": c.model_dump(), "news": fetch_nse_news(c.symbol)} for c in candidates]
    prompt = f"""You are AG4, Chief Analyst for NSE India. Audit all candidates from AG1 (Low), AG2 (Mid), and AG3 (High) with live news.
    Mark each as BUY or SKIP. Reject stocks with negative news or weak risk/reward (< 1:1.8). Inputs: {json.dumps(enriched)}"""
    resp = await client.aio.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=CommitteeOutput, temperature=0.1)
    )
    decision = CommitteeOutput(**json.loads(resp.text))
    buys = [d for d in decision.selected_trades if d.action == "BUY"]
    log_msg(cycle_id, "AG4_CHIEF_ANALYST", "AG5_EXECUTION_BROKER", "VERDICT",
            f"Approved {len(buys)} BUY order(s) out of {len(candidates)} proposals.", decision.model_dump())
    return decision

def ag5_broker_step(cycle_id: str, decisions: list[FinalDecision], live_prices: dict[str, float]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cash = cur.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]

    # 1. Monitor open positions for SL/TP exits
    for tid, sym, qty, entry, sl, target in cur.execute("SELECT id, symbol, qty, entry, sl, target FROM trades WHERE status='OPEN'").fetchall():
        ltp = live_prices.get(sym)
        if ltp and (ltp <= sl or ltp >= target):
            outcome = "TARGET_HIT" if ltp >= target else "SL_HIT"
            pnl = round((ltp - entry) * qty, 2)
            cash += ltp * qty
            cur.execute("UPDATE account SET cash=?", (cash,))
            cur.execute("UPDATE trades SET status=?, exit_price=?, pnl=? WHERE id=?", (outcome, ltp, pnl, tid))
            log_msg(cycle_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", outcome,
                    f"SOLD {sym} ({outcome}) @ ₹{ltp} | Net P&L: ₹{pnl}", {"symbol": sym, "pnl": pnl})

    # 2. Execute new BUY orders
    for d in decisions:
        if d.action != "BUY" or d.conviction < 70:
            continue
        if cur.execute("SELECT id FROM trades WHERE symbol=? AND status='OPEN'", (d.symbol,)).fetchone():
            continue
        risk_per_share = max(d.entry_price - d.stop_loss, d.entry_price * 0.005)
        qty = int((cash * RISK_PER_TIER[d.risk_tier]) // risk_per_share)
        cost = qty * d.entry_price
        if qty > 0 and cost <= cash * 0.25:
            cash -= cost
            cur.execute("UPDATE account SET cash=?", (cash,))
            cur.execute("INSERT INTO trades (timestamp, symbol, tier, status, qty, entry, sl, target, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), d.symbol, d.risk_tier, "OPEN", qty, d.entry_price, d.stop_loss, d.target_price, d.reasoning))
            log_msg(cycle_id, "AG5_EXECUTION_BROKER", "PORTFOLIO_LEDGER", "PAPER_BUY",
                    f"BOUGHT {qty} {d.symbol} ({d.risk_tier}) @ ₹{d.entry_price} | SL: ₹{d.stop_loss} | TP: ₹{d.target_price}", d.model_dump())

    conn.commit()
    conn.close()

async def run_full_cycle(api_key: str):
    client = genai.Client(api_key=api_key)
    cycle_id = datetime.now().strftime("CYC-%H%M%S")
    market_data = fetch_nse_metrics()
    live_prices = {m["symbol"]: m["ltp"] for m in market_data}

    ag1_slice = [d for d in market_data if d["beta"] <= 0.95 and d["ltp"] >= d["sma_200"]]
    ag2_slice = [d for d in market_data if 0.9 < d["beta"] <= 1.45 and d["rvol"] >= 1.1]
    ag3_slice = [d for d in market_data if d["beta"] > 1.3 or abs(d["day_change_pct"]) >= 2.0]

    ag1, ag2, ag3 = await asyncio.gather(
        run_screener(client, cycle_id, "AG1_LOW_RISK", "Low Beta (<0.95), above 200-SMA, steady compounding.", ag1_slice),
        run_screener(client, cycle_id, "AG2_MID_RISK", "Strong RVOL (>1.1x), above 50-SMA, momentum swing.", ag2_slice),
        run_screener(client, cycle_id, "AG3_HIGH_RISK", "High beta or sharp daily mover (>2%), tight 1x ATR stop.", ag3_slice),
    )
    candidates = ag1 + ag2 + ag3
    decisions = (await run_ag4(client, cycle_id, candidates)).selected_trades if candidates else []
    ag5_broker_step(cycle_id, decisions, live_prices)
