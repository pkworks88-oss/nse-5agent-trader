import streamlit as st
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from swing_engine import (
    get_gsheet_client,
    SWING_SHEET_NAME,
    MAX_OPEN_POSITIONS,
    run_full_cycle,
    compute_agent_trust_scores,
)
from intraday_engine import (
    INTRADAY_SHEET_NAME,
    INIT_EQ_CAPITAL,
    INIT_FNO_CAPITAL,
    ensure_intraday_ledger_initialized,
    run_unified_cycle,
)
from broker_adapter import get_broker_adapter

IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def is_nse_market_open(now_dt: datetime) -> bool:
    if now_dt.weekday() >= 5:
        return False
    mins = now_dt.hour * 60 + now_dt.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


st.set_page_config(
    page_title="NSE ₹12L Master Multi-Asset 5-Agent Platform",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    section[data-testid="stSidebar"] { background-color: #081020; border-right: 1px solid #1e293b; }
    .top-bar { display: flex; justify-content: space-between; align-items: center; background: linear-gradient(90deg, #0d172a 0%, #111f38 100%); border: 1px solid #1e3a5f; border-radius: 12px; padding: 14px 20px; margin-bottom: 14px; }
    .platform-title { font-size: 22px; font-weight: 800; color: #f8fafc; margin: 0; }
    .live-pill { background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; color: #34d399; padding: 4px 12px; border-radius: 20px; font-size: 12.5px; font-weight: 600; }
    .book-card { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; min-height: 175px; }
    .book-title { font-size: 13px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
    .book-val { font-size: 26px; font-weight: 800; color: #f8fafc; margin: 6px 0; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=90)
def get_indian_indices():
    indices = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "BANK NIFTY": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}
    out = {}
    for name, sym in indices.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                curr, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                diff = curr - prev
                out[name] = (curr, (diff / prev) * 100)
            else:
                out[name] = (0.0, 0.0)
        except Exception:
            out[name] = (0.0, 0.0)
    return out


@st.cache_data(ttl=60)
def get_live_ltps(symbols: tuple[str, ...]) -> dict[str, float]:
    prices = {}
    if not symbols:
        return prices
    try:
        data = yf.download(list(symbols), period="2d", interval="15m", group_by="ticker", progress=False, threads=True)
        for sym in symbols:
            try:
                sub = data[sym]["Close"].dropna() if len(symbols) > 1 else data["Close"].dropna()
                if not sub.empty:
                    prices[sym] = round(float(sub.iloc[-1]), 2)
            except Exception:
                continue
    except Exception:
        pass
    return prices


current_ist = get_ist_now()
market_open = is_nse_market_open(current_ist)

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()

# 1. Connect to Book 1: ₹10L Swing Ledger (NSE_5Agent_Ledger)
swing_sh = get_gsheet_client(gcp_creds, SWING_SHEET_NAME)
swing_cash = float(swing_sh.worksheet("account").acell("A2").value or 1_000_000.0)
swing_trades = swing_sh.worksheet("trades").get_all_records()
swing_trust = compute_agent_trust_scores(swing_trades)

swing_df = pd.DataFrame(swing_trades) if swing_trades else pd.DataFrame()
open_swing = swing_df[swing_df["status"] == "OPEN"].copy() if not swing_df.empty else pd.DataFrame()
closed_swing = swing_df[swing_df["status"] != "OPEN"].copy() if not swing_df.empty else pd.DataFrame()

if not open_swing.empty:
    ltp_map = get_live_ltps(tuple(sorted(open_swing["symbol"].unique().tolist())))
    open_swing["qty"] = open_swing["qty"].astype(float)
    open_swing["entry"] = open_swing["entry"].astype(float)
    open_swing["live_ltp"] = open_swing["symbol"].map(ltp_map).fillna(open_swing["entry"])
    swing_holdings_val = float((open_swing["qty"] * open_swing["live_ltp"]).sum())
    swing_unrealized = float(((open_swing["live_ltp"] - open_swing["entry"]) * open_swing["qty"]).sum())
else:
    swing_holdings_val = 0.0
    swing_unrealized = 0.0

swing_realized = float(closed_swing["pnl"].astype(float).sum()) if not closed_swing.empty else 0.0
swing_equity = swing_cash + swing_holdings_val

# 2. Connect to Book 2: ₹2L Unified Intraday MIS + Index F&O Ledger (NSE_Unified_2L_Ledger)
try:
    intra_sh = get_gsheet_client(gcp_creds, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(intra_sh)
    intra_acct = intra_sh.worksheet("account")
    eq_cash = float(intra_acct.acell("B2").value or INIT_EQ_CAPITAL)
    fno_cash = float(intra_acct.acell("C2").value or INIT_FNO_CAPITAL)
    intra_trades = intra_sh.worksheet("trades").get_all_records()
    intra_df = pd.DataFrame(intra_trades) if intra_trades else pd.DataFrame()
    open_intra = intra_df[intra_df["status"] == "OPEN"] if not intra_df.empty else pd.DataFrame()
    closed_intra = intra_df[intra_df["status"] != "OPEN"] if not intra_df.empty else pd.DataFrame()
    eq_open_margin = float(
        sum((float(r["entry"]) * int(r["qty_or_lots"])) / 5.0 for _, r in open_intra.iterrows() if r.get("book") == "EQUITY")
    ) if not open_intra.empty else 0.0
    fno_open_prem = float(
        sum(float(r["entry"]) * int(r["qty_or_lots"]) * int(r.get("lot_size", 1) or 1) for _, r in open_intra.iterrows() if r.get("book") == "FNO")
    ) if not open_intra.empty else 0.0
    intra_realized = float(closed_intra["net_pnl"].astype(float).sum()) if not closed_intra.empty else 0.0
    intra_connected = True
except Exception as e:
    eq_cash, fno_cash = INIT_EQ_CAPITAL, INIT_FNO_CAPITAL
    eq_open_margin, fno_open_prem, intra_realized = 0.0, 0.0, 0.0
    open_intra = pd.DataFrame()
    intra_connected = False
    intra_err = str(e)

eq_book_equity = eq_cash + eq_open_margin
fno_book_equity = fno_cash + fno_open_prem
master_total_equity = swing_equity + eq_book_equity + fno_book_equity
master_base = 1_200_000.0
master_net_pnl = master_total_equity - master_base
master_ret_pct = (master_net_pnl / master_base) * 100.0

idx = get_indian_indices()
badge = f"● NSE Open • {current_ist.strftime('%I:%M:%S %p IST')}" if market_open else f"● NSE Closed • {current_ist.strftime('%I:%M:%S %p IST')}"

st.markdown(f"""
<div class="top-bar">
    <div>
        <div class="platform-title">🏛️ NSE ₹12,00,000 Unified Multi-Asset 5-Agent Master Platform</div>
        <div style="color:#94a3b8;font-size:13px;margin-top:4px;">
            <b>NIFTY 50:</b> {idx['NIFTY 50'][0]:,.2f} ({idx['NIFTY 50'][1]:+.2f}%) &nbsp;|&nbsp;
            <b>BANK NIFTY:</b> {idx['BANK NIFTY'][0]:,.2f} ({idx['BANK NIFTY'][1]:+.2f}%) &nbsp;|&nbsp;
            <b>INDIA VIX:</b> {idx['INDIA VIX'][0]:.2f} &nbsp;|&nbsp;
            <b>Master Net Worth:</b> <span style="color:#34d399;font-weight:700;">₹{master_total_equity:,.2f} ({master_ret_pct:+.2f}%)</span>
        </div>
    </div>
    <div><span class="live-pill">{badge}</span></div>
</div>
""", unsafe_allow_html=True)

if not intra_connected:
    st.warning(f"Could not connect to `{INTRADAY_SHEET_NAME}`: {intra_err}. Ensure the Google Sheet is shared with your service account.")

# Master Action Bar
m1, m2, m3, m4 = st.columns([1.3, 1.3, 1.1, 1.3])
with m1:
    if st.button("📈 Run ₹10L Swing Cycle", use_container_width=True, type="primary"):
        with st.spinner("Running 250-stock Swing Cycle (AG1–AG5)..."):
            asyncio.run(run_full_cycle(api_key, gcp_creds))
        st.cache_data.clear()
        st.toast("₹10L Swing Cycle Complete!", icon="✅")
        st.rerun()
with m2:
    if st.button("⚡ Run ₹2L Intraday + F&O Cycle", use_container_width=True):
        with st.spinner("Scanning 61 MIS stocks + NIFTY/BANKNIFTY Option Chains..."):
            asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=False))
        st.cache_data.clear()
        st.toast("₹2L Intraday + F&O Cycle Complete!", icon="✅")
        st.rerun()
with m3:
    if st.button("🔄 Refresh All Books", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with m4:
    if st.button("🚨 MASTER EMERGENCY SQUARE-OFF", use_container_width=True):
        asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=True))
        adapter = get_broker_adapter("DHAN", live_mode=False)
        adapter.cancel_all_and_square_off()
        st.cache_data.clear()
        st.error("Master Emergency Square-Off & Broker Kill-Switch Triggered!")

st.markdown("### 💼 Ring-Fenced Multi-Asset Capital Books (₹12,00,000 Base)")
b1, b2, b3, b4 = st.columns(4)
with b1:
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #10b981;">
        <div class="book-title">🏛️ Master Combined Equity</div>
        <div class="book-val">₹{master_total_equity:,.2f}</div>
        <div style="color:{'#34d399' if master_net_pnl >= 0 else '#f87171'};font-size:13px;font-weight:700;">Net P&L: ₹{master_net_pnl:+,.2f} ({master_ret_pct:+.2f}%)</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">3 Ring-Fenced Wallets • Shared AG1–AG5 Brain</div>
    </div>
    """, unsafe_allow_html=True)
with b2:
    sw_pnl = swing_equity - 1_000_000.0
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #3b82f6;">
        <div class="book-title">📈 Desk 1: Positional Swing (₹10L)</div>
        <div class="book-val">₹{swing_equity:,.2f}</div>
        <div style="color:{'#34d399' if sw_pnl >= 0 else '#f87171'};font-size:13px;">Unrealized: ₹{swing_unrealized:+,.2f} | Realized: ₹{swing_realized:+,.2f}</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Open Slots: <b>{len(open_swing)} / {MAX_OPEN_POSITIONS}</b> • Cash: ₹{swing_cash:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)
with b3:
    eq_pnl = eq_book_equity - INIT_EQ_CAPITAL
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #f59e0b;">
        <div class="book-title">⚡ Book A: Intraday Equity MIS (₹1L)</div>
        <div class="book-val">₹{eq_book_equity:,.2f}</div>
        <div style="color:{'#34d399' if eq_pnl >= 0 else '#f87171'};font-size:13px;">Net Return: ₹{eq_pnl:+,.2f} (5x Buying Power: ₹{eq_cash*5:,.0f})</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Circuit Breaker: -₹1,500 • Mandatory 3:15 PM Exit</div>
    </div>
    """, unsafe_allow_html=True)
with b4:
    fno_pnl = fno_book_equity - INIT_FNO_CAPITAL
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #a855f7;">
        <div class="book-title">🎯 Book B: Index F&O Options (₹1L)</div>
        <div class="book-val">₹{fno_book_equity:,.2f}</div>
        <div style="color:{'#34d399' if fno_pnl >= 0 else '#f87171'};font-size:13px;">Net Return: ₹{fno_pnl:+,.2f} (NIFTY & BANKNIFTY)</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Circuit Breaker: -₹2,000 • VIX-Adaptive Spreads</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
c_left, c_right = st.columns([1.4, 1.6])
with c_left:
    st.markdown("#### 📊 Capital Allocation Breakdown")
    fig_pie = go.Figure(data=[go.Pie(
        labels=["Swing Equity Holdings", "Swing Free Cash", "Intraday MIS Wallet", "Index F&O Wallet"],
        values=[max(swing_holdings_val, 0), max(swing_cash, 0), max(eq_book_equity, 0), max(fno_book_equity, 0)],
        hole=0.55,
        marker=dict(colors=["#10b981", "#3b82f6", "#f59e0b", "#a855f7"]),
    )])
    fig_pie.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b1324",
        plot_bgcolor="#0b1324",
        height=280,
        margin=dict(l=10, r=10, t=15, b=10),
    )
    st.plotly_chart(fig_pie, use_container_width=True)

with c_right:
    st.markdown("#### 🤖 Shared 5-Agent Council & Trust Standings")
    t1, t2, t3 = st.columns(3)
    for col_o, k in zip([t1, t2, t3], ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]):
        info = swing_trust[k]
        with col_o:
            st.metric(info["label"], f"Trust: {info['score']}/100", f"{info['wins']}W-{info['losses']}L | ₹{info['net_pnl']:+,.0f}")

    st.info(
        "👈 **Use the left sidebar** to switch between:\n"
        "- **`1_Swing_10L_Desk`**: Full V3.1 250-stock Swing Terminal, Pro Gap-Free Candlestick + VWAP/EMA/RSI Charts, Backtester, and Broker Bridge.\n"
        "- **`2_Intraday_2L_Desk`**: ₹1L MIS Equities + ₹1L NIFTY/BANKNIFTY Option Chain Matrix, Payoff Simulator, and 20s Watchdog Logs."
    )
