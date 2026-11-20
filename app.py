import streamlit as st
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from engine import (
    run_full_cycle,
    get_gsheet_client,
    now_ist,
    compute_agent_trust_scores,
    infer_trade_agent,
    record_trade_reflection,
)

IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


st.set_page_config(
    page_title="5-Agent Intelligent Stock Trading Platform",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- RESPONSIVE CYBER-TERMINAL STYLING (LAPTOP + MOBILE) ---
st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    section[data-testid="stSidebar"] { background-color: #081020; border-right: 1px solid #1e293b; }
    .top-bar { display: flex; justify-content: space-between; align-items: center; background: linear-gradient(90deg, #0d172a 0%, #111f38 100%); border: 1px solid #1e3a5f; border-radius: 12px; padding: 12px 20px; margin-bottom: 12px; }
    .platform-title { font-size: 21px; font-weight: 800; color: #f8fafc; margin: 0; }
    .top-ticker-strip { margin-left: 14px; color: #94a3b8; font-size: 13px; }
    .live-pill { background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; color: #34d399; padding: 4px 12px; border-radius: 20px; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
    .wf-card { border-radius: 12px; padding: 14px 10px; text-align: center; background: #0b1426; min-height: 148px; }
    .wf-green { border: 1.5px solid #10b981; box-shadow: 0 0 15px rgba(16, 185, 129, 0.22); }
    .wf-blue  { border: 1.5px solid #3b82f6; box-shadow: 0 0 15px rgba(59, 130, 246, 0.22); }
    .wf-orange{ border: 1.5px solid #f97316; box-shadow: 0 0 15px rgba(249, 115, 22, 0.22); }
    .wf-purple{ border: 1.5px solid #a855f7; box-shadow: 0 0 15px rgba(168, 85, 247, 0.22); }
    .wf-teal  { border: 1.5px solid #06b6d4; box-shadow: 0 0 15px rgba(6, 182, 212, 0.22); }
    .wf-title { font-size: 17px; font-weight: 800; color: #ffffff; margin: 4px 0; }
    .wf-sub   { font-size: 11.5px; color: #94a3b8; margin-bottom: 4px; }
    .wf-stat  { font-size: 12px; font-weight: 600; color: #34d399; }
    .trust-pill { display: inline-block; margin-top: 5px; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; background: #132342; color: #38bdf8; border: 1px solid #1e3a5f; }
    .wf-arrow { text-align: center; padding-top: 56px; font-size: 22px; color: #64748b; }
    .agent-col { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 12px; min-height: 290px; }
    .chat-bubble { background: #131f38; border-left: 3px solid #38bdf8; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 12.5px; color: #e2e8f0; }
    .lesson-card { background: #111b33; border-left: 3px solid #f59e0b; border-radius: 8px; padding: 9px 12px; margin-bottom: 8px; font-size: 12.5px; color: #e2e8f0; }
    .chat-time { font-size: 10.5px; color: #64748b; display: block; margin-bottom: 2px; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }
    .rec-ticker { font-size: 24px; font-weight: 800; color: #34d399; letter-spacing: 0.5px; }
    .metric-big { font-size: 23px; font-weight: 800; color: #f8fafc; }
    .status-strip { text-align: right; padding-top: 6px; color: #94a3b8; font-size: 12.5px; }

    div[data-testid="stTabs"] button {
        background-color: #0d172a;
        border: 1px solid #1e293b;
        border-radius: 8px 8px 0 0;
        color: #94a3b8;
        font-weight: 600;
        padding: 8px 18px;
    }
    div[data-testid="stTabs"] button[aria-selected="true"] {
        background-color: #132342;
        border-color: #38bdf8;
        color: #ffffff;
    }

    /* MOBILE PHONE ADAPTATIONS (<= 768px) */
    @media (max-width: 768px) {
        .block-container { padding-top: 1rem !important; padding-left: 0.75rem !important; padding-right: 0.75rem !important; }
        .top-bar { flex-direction: column; align-items: flex-start; gap: 8px; padding: 12px 14px; }
        .platform-title { font-size: 17px; line-height: 1.3; }
        .top-ticker-strip { margin-left: 0; display: block; margin-top: 4px; font-size: 12px; }
        .live-pill { font-size: 11.5px; padding: 3px 10px; }
        .status-strip { text-align: left; padding-top: 4px; font-size: 12px; }
        .wf-card { min-height: auto; padding: 10px 8px; margin-bottom: 4px; }
        .wf-arrow { display: none !important; }
        .agent-col { min-height: auto; margin-bottom: 8px; padding: 10px; }
        .panel-box { min-height: auto !important; margin-bottom: 10px; padding: 12px; }
        .rec-ticker { font-size: 20px; }
        .metric-big { font-size: 19px; }
        div[data-testid="stTabs"] button { padding: 6px 10px; font-size: 12px; }
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=90)
def get_indian_indices():
    indices = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "BANK NIFTY": "^NSEBANK"}
    out = {}
    for name, sym in indices.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                curr, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                diff = curr - prev
                out[name] = (curr, diff, (diff / prev) * 100)
            else:
                out[name] = (25280.05, 105.20, 0.42)
        except Exception:
            out[name] = (25280.05, 105.20, 0.42)
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


@st.cache_data(ttl=60)
def get_stock_chart_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        return df.dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=180)
def get_nifty_benchmark_series(start_date_str: str) -> pd.DataFrame:
    try:
        start_dt = pd.to_datetime(start_date_str) - timedelta(days=2)
        df = yf.Ticker("^NSEI").history(start=start_dt.strftime("%Y-%m-%d"), interval="1d")
        return df.dropna()
    except Exception:
        return pd.DataFrame()


def is_nse_market_open(now_dt: datetime) -> bool:
    if now_dt.weekday() >= 5:
        return False
    mins = now_dt.hour * 60 + now_dt.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


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

sh = get_gsheet_client(gcp_creds)
cash = float(sh.worksheet("account").acell("A2").value or 1_000_000.0)
trades_records = sh.worksheet("trades").get_all_records()
msgs_records = sh.worksheet("agent_messages").get_all_records()

trust_scores = compute_agent_trust_scores(trades_records)

trades_df = pd.DataFrame(trades_records).iloc[::-1] if trades_records else pd.DataFrame()
msgs_df = pd.DataFrame(msgs_records).iloc[::-1].head(150) if msgs_records else pd.DataFrame()

open_t = trades_df[trades_df["status"] == "OPEN"].copy() if not trades_df.empty else pd.DataFrame()
closed_t = trades_df[trades_df["status"] != "OPEN"].copy() if not trades_df.empty else pd.DataFrame()

open_symbols_tuple = tuple(sorted(open_t["symbol"].unique().tolist())) if not open_t.empty else ()
live_ltp_map = get_live_ltps(open_symbols_tuple)

if not open_t.empty:
    open_t["qty"] = open_t["qty"].astype(float)
    open_t["entry"] = open_t["entry"].astype(float)
    open_t["sl"] = open_t["sl"].astype(float)
    open_t["target"] = open_t["target"].astype(float)
    open_t["live_ltp"] = open_t["symbol"].map(live_ltp_map).fillna(open_t["entry"])
    open_t["unrealized_pnl"] = ((open_t["live_ltp"] - open_t["entry"]) * open_t["qty"]).round(2)
    open_t["pnl_pct"] = (((open_t["live_ltp"] - open_t["entry"]) / open_t["entry"]) * 100).round(2)
    current_holdings_val = (open_t["qty"] * open_t["live_ltp"]).sum()
    unrealized_pnl_total = open_t["unrealized_pnl"].sum()
else:
    current_holdings_val = 0.0
    unrealized_pnl_total = 0.0

realized_pnl = closed_t["pnl"].astype(float).sum() if not closed_t.empty else 0.0
total_portfolio = cash + current_holdings_val
overall_ret_pct = ((total_portfolio - 1_000_000.0) / 1_000_000.0) * 100

indices = get_indian_indices()
nifty_val, _, nifty_pct = indices["NIFTY 50"]
sensex_val, _, sensex_pct = indices["SENSEX"]
bnifty_val, _, bnifty_pct = indices["BANK NIFTY"]

market_badge_text = (
    f"● NSE Open • {current_ist.strftime('%I:%M:%S %p IST')}"
    if market_open
    else f"● NSE Closed • {current_ist.strftime('%I:%M:%S %p IST')}"
)

st.markdown(f"""
<div class="top-bar">
    <div>
        <span class="platform-title">5-Agent Intelligent Stock Trading Platform</span>
        <span class="top-ticker-strip">
            <b>NIFTY 50</b> {nifty_val:,.2f} <span style="color:#34d399;">({nifty_pct:+.2f}%)</span> &nbsp;|&nbsp;
            <b>SENSEX</b> {sensex_val:,.2f} <span style="color:#34d399;">({sensex_pct:+.2f}%)</span> &nbsp;|&nbsp;
            <b>Equity:</b> <span style="color:#f8fafc;">₹{total_portfolio:,.2f}</span>
        </span>
    </div>
    <div><span class="live-pill">{market_badge_text}</span></div>
</div>
""", unsafe_allow_html=True)

ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([1.4, 1.1, 1.3, 2.2])
with ctrl1:
    if st.button("⚡ Run 5-Agent Cycle Now", use_container_width=True, type="primary"):
        with st.spinner("Scanning 250 NSE stocks + Trust-weighted AG1–AG5 executing..."):
            asyncio.run(run_full_cycle(api_key, gcp_creds))
        st.cache_data.clear()
        st.toast("250-Stock Cycle Completed!", icon="✅")
        st.rerun()
with ctrl2:
    if st.button("🔄 Refresh Prices", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with ctrl3:
    auto_refresh = st.toggle("⏱️ Auto-Refresh (60s)", value=False)
with ctrl4:
    st.markdown(
        f"<div class='status-strip'>"
        f"📅 {current_ist.strftime('%d %b %Y (%a)')} &nbsp;|&nbsp; Cash: <b style='color:#f8fafc;'>₹{cash:,.2f}</b> &nbsp;|&nbsp; "
        f"Unrealized P&L: <b style='color:{'#34d399' if unrealized_pnl_total >= 0 else '#f87171'};'>₹{unrealized_pnl_total:+,.2f}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )


def get_agent_msgs(sender_code: str, limit: int = 3):
    if msgs_df.empty:
        return []
    return msgs_df[msgs_df["sender"] == sender_code].head(limit).to_dict("records")


ag1_msgs = get_agent_msgs("AG1_LOW_RISK", 3)
ag2_msgs = get_agent_msgs("AG2_MID_RISK", 3)
ag3_msgs = get_agent_msgs("AG3_HIGH_RISK", 3)
ag4_msgs = get_agent_msgs("AG4_CHIEF_ANALYST", 3)
ag5_msgs = get_agent_msgs("AG5_EXECUTION_BROKER", 3)
lesson_msgs = msgs_df[msgs_df["msg_type"] == "AGENT_LESSON"].head(6).to_dict("records") if not msgs_df.empty else []

tab_dash, tab_charts, tab_port, tab_agents, tab_logs = st.tabs([
    "🏠 Command Center",
    "📈 Live Charts",
    "💼 Portfolio & P&L",
    "🤖 Agents & Trust",
    "📜 Ledger",
])

with tab_dash:
    top_left, top_right = st.columns([3.3, 1.3])
    with top_left:
        st.markdown("#### ⚡ 5-Agent Workflow & Trust Standings (24/7 Cloud)")
        w1, a1, w2, a2, w3, a3, w4, a4, w5 = st.columns([1.8, 0.3, 1.8, 0.3, 1.8, 0.3, 2.0, 0.3, 1.8])
        t1 = trust_scores["AG1_LOW_RISK"]["score"]
        t2 = trust_scores["AG2_MID_RISK"]["score"]
        t3 = trust_scores["AG3_HIGH_RISK"]["score"]
        with w1:
            st.markdown(f"<div class='wf-card wf-green'><div style='font-size:20px;'>🛡️</div><div class='wf-title'>AG1</div><div class='wf-sub'>Low Risk Analyst</div><div class='wf-stat'>✓ {'Active' if ag1_msgs else 'Standby'}</div><span class='trust-pill'>Trust {t1}/100</span></div>", unsafe_allow_html=True)
        with a1:
            st.markdown("<div class='wf-arrow'>→</div>", unsafe_allow_html=True)
        with w2:
            st.markdown(f"<div class='wf-card wf-blue'><div style='font-size:20px;'>📈</div><div class='wf-title'>AG2</div><div class='wf-sub'>Mid Risk Analyst</div><div class='wf-stat'>✓ {'Active' if ag2_msgs else 'Standby'}</div><span class='trust-pill'>Trust {t2}/100</span></div>", unsafe_allow_html=True)
        with a2:
            st.markdown("<div class='wf-arrow'>→</div>", unsafe_allow_html=True)
        with w3:
            st.markdown(f"<div class='wf-card wf-orange'><div style='font-size:20px;'>🚀</div><div class='wf-title'>AG3</div><div class='wf-sub'>High Risk Analyst</div><div class='wf-stat'>✓ {'Active' if ag3_msgs else 'Standby'}</div><span class='trust-pill'>Trust {t3}/100</span></div>", unsafe_allow_html=True)
        with a3:
            st.markdown("<div class='wf-arrow' style='color:#a855f7;'>⇉</div>", unsafe_allow_html=True)
        with w4:
            st.markdown(f"<div class='wf-card wf-purple'><div style='font-size:20px;'>🧠</div><div class='wf-title'>AG4</div><div class='wf-sub'>Chief Analyst</div><div class='wf-stat'>✓ {'Verified' if ag4_msgs else 'Waiting'}</div><span class='trust-pill'>Trust-Weighted</span></div>", unsafe_allow_html=True)
        with a4:
            st.markdown("<div class='wf-arrow' style='color:#06b6d4;'>→</div>", unsafe_allow_html=True)
        with w5:
            st.markdown(f"<div class='wf-card wf-teal'><div style='font-size:20px;'>🛒</div><div class='wf-title'>AG5</div><div class='wf-sub'>Execute & Watchdog</div><div class='wf-stat'>✓ {len(open_t)}/10 Open</div><span class='trust-pill'>25%+10% Ladder</span></div>", unsafe_allow_html=True)

    with top_right:
        st.markdown("#### 📊 Live Market Snapshot")
        st.markdown(f"""
        <div class="panel-box" style="min-height:148px;padding:12px;">
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:8px;"><span style="color:#94a3b8;">NIFTY 50</span><span><b>{nifty_val:,.2f}</b> <span style="color:#34d399;">{nifty_pct:+.2f}%</span></span></div>
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:8px;"><span style="color:#94a3b8;">SENSEX</span><span><b>{sensex_val:,.2f}</b> <span style="color:#34d399;">{sensex_pct:+.2f}%</span></span></div>
            <div style="display:flex;justify-content:space-between;font-size:13px;"><span style="color:#94a3b8;">BANK NIFTY</span><span><b>{bnifty_val:,.2f}</b> <span style="color:#34d399;">{bnifty_pct:+.2f}%</span></span></div>
        </div>
        """, unsafe_allow_html=True)

    # --- BENCHMARK CURVE (PORTFOLIO EQUITY VS NIFTY 50) + WHAT THEY HAVE LEARNED ---
    st.markdown("<br>", unsafe_allow_html=True)
    bench_col, learn_col = st.columns([1.8, 1.2])
    with bench_col:
        st.markdown("#### 📈 Portfolio Equity vs. NIFTY 50 Benchmark (₹10,00,000 Base)")
        if trades_records:
            chron_trades = pd.DataFrame(trades_records)
            chron_trades["dt"] = pd.to_datetime(chron_trades["timestamp"].str.replace(" IST", "", regex=False), errors="coerce")
            chron_trades = chron_trades.dropna(subset=["dt"]).sort_values("dt")
            start_str = chron_trades["dt"].min().strftime("%Y-%m-%d")
            nifty_hist = get_nifty_benchmark_series(start_str)

            eq_dates = [chron_trades["dt"].min() - timedelta(hours=1)]
            eq_vals = [1_000_000.0]
            running_realized = 0.0
            for _, tr in chron_trades.iterrows():
                if tr.get("status") != "OPEN":
                    running_realized += float(tr.get("pnl", 0) or 0)
                    eq_dates.append(tr["dt"])
                    eq_vals.append(1_000_000.0 + running_realized)
            eq_dates.append(current_ist.replace(tzinfo=None))
            eq_vals.append(total_portfolio)

            fig_bench = go.Figure()
            fig_bench.add_trace(go.Scatter(
                x=eq_dates,
                y=eq_vals,
                mode="lines+markers",
                name="5-Agent Portfolio (₹)",
                line=dict(color="#10b981", width=3),
                marker=dict(size=6),
            ))

            if not nifty_hist.empty:
                n_idx = nifty_hist.index.tz_localize(None)
                base_nifty = float(nifty_hist["Close"].iloc[0])
                scaled_nifty = (nifty_hist["Close"] / base_nifty) * 1_000_000.0
                fig_bench.add_trace(go.Scatter(
                    x=n_idx,
                    y=scaled_nifty,
                    mode="lines",
                    name="NIFTY 50 Buy & Hold (₹10L Eq.)",
                    line=dict(color="#94a3b8", width=2, dash="dash"),
                ))

            fig_bench.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0b1324",
                plot_bgcolor="#081020",
                height=265,
                margin=dict(l=10, r=10, t=14, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis_title="Account Value (₹)",
            )
            st.plotly_chart(fig_bench, use_container_width=True)
        else:
            st.markdown("<div class='panel-box' style='min-height:265px;'><p style='color:#94a3b8;'>Run your first cycle to begin plotting Portfolio vs. NIFTY 50 benchmark performance.</p></div>", unsafe_allow_html=True)

    with learn_col:
        st.markdown("#### 🧠 What They Have Learned (Self-Reflection Feed)")
        if not lesson_msgs:
            st.markdown(
                "<div class='panel-box' style='min-height:265px;'>"
                "<div class='lesson-card'>🤖 <b>Self-Reflection Engine Armed:</b> Whenever any trade closes in profit or loss, the responsible agent records a 1-line post-mortem lesson here and updates its Trust Score (0–100) for future cycles.</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            lessons_html = "<div class='panel-box' style='min-height:265px;max-height:265px;overflow-y:auto;'>"
            for lm in lesson_msgs[:4]:
                ts_parts = str(lm.get("timestamp", "")).split(" ")
                ts_short = " ".join(ts_parts[1:]) if len(ts_parts) > 1 else str(lm.get("timestamp", ""))
                lessons_html += f"<div class='lesson-card'><span class='chat-time'>🎓 {ts_short}</span>{lm['summary']}</div>"
            lessons_html += "</div>"
            st.markdown(lessons_html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 💬 Agent Communication — Live Chat Stream (IST)")
    c_ag1, c_ag2, c_ag3, c_ag4, c_ag5 = st.columns(5)

    def render_agent_column(col, title, border_color, records, empty_hint):
        with col:
            bubbles_html = ""
            if not records:
                bubbles_html = f"<div class='chat-bubble' style='border-left-color:{border_color};'>{empty_hint}</div>"
            else:
                for r in records:
                    parts = str(r["timestamp"]).split(" ")
                    ts = " ".join(parts[1:]) if len(parts) > 1 else str(r["timestamp"])
                    bubbles_html += (
                        f"<div class='chat-bubble' style='border-left-color:{border_color};'>"
                        f"<span class='chat-time'>🤖 {ts}</span>{r['summary']}</div>"
                    )
            st.markdown(
                f"<div class='agent-col' style='border-top: 3px solid {border_color};'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>"
                f"<b style='font-size:13.5px;color:#f8fafc;'>{title}</b>"
                f"<span style='color:#34d399;font-size:11px;'>● Online</span></div>"
                f"{bubbles_html}</div>",
                unsafe_allow_html=True,
            )

    render_agent_column(c_ag1, "🛡️ AG1 – Low Risk", "#10b981", ag1_msgs, "Scanning low-beta NIFTY 200 + VWAP...")
    render_agent_column(c_ag2, "📈 AG2 – Mid Risk", "#3b82f6", ag2_msgs, "Scanning momentum & RVOL + VWAP...")
    render_agent_column(c_ag3, "🚀 AG3 – High Risk", "#f97316", ag3_msgs, "Scanning high-beta intraday movers...")
    render_agent_column(c_ag4, "🧠 AG4 – Chief Analyst", "#a855f7", ag4_msgs, "Auditing trust weights, news & regime...")
    render_agent_column(c_ag5, "⚡ AG5 – Executor", "#06b6d4", ag5_msgs, "30s Watchdog & 25%+10% Ladder active...")

    st.markdown("<br>", unsafe_allow_html=True)
    b1, b2, b3 = st.columns([1.2, 1.3, 1.5])
    with b1:
        st.markdown("#### 🏆 Final Recommendation (AG4)")
        latest_buy = None
        if ag4_msgs:
            try:
                payload = json.loads(ag4_msgs[0]["payload_json"])
                buys = [t for t in payload.get("selected_trades", []) if t.get("action") == "BUY"]
                if buys:
                    latest_buy = buys[0]
            except Exception:
                pass
        if latest_buy:
            rr_val = round((latest_buy["target_price"] - latest_buy["entry_price"]) / max(latest_buy["entry_price"] - latest_buy["stop_loss"], 0.01), 1)
            src_ag = latest_buy.get("source_agent", "AG4")
            st.markdown(f"""
            <div class="panel-box" style="min-height:250px;">
                <div class="rec-ticker">{latest_buy['symbol']}</div>
                <div style="color:#94a3b8;font-size:12px;margin-bottom:10px;">Tier: <b>{latest_buy['risk_tier']}</b> ({src_ag}) • Conviction: <b>{latest_buy['conviction']}%</b></div>
                <div style="font-size:13px;line-height:1.8;">
                    <b>Entry Price:</b> ₹{latest_buy['entry_price']:,.2f}<br>
                    <b>Stop Loss:</b> <span style="color:#f87171;">₹{latest_buy['stop_loss']:,.2f}</span><br>
                    <b>Target Price:</b> <span style="color:#34d399;">₹{latest_buy['target_price']:,.2f}</span><br>
                    <b>Risk / Reward:</b> 1 : {rr_val}
                </div>
                <div style="margin-top:10px;padding:8px;background:#131f38;border-radius:8px;font-size:12px;color:#cbd5e1;">✓ {latest_buy['reasoning']}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("<div class='panel-box' style='min-height:250px;'><div class='rec-ticker'>WATCHDOG ACTIVE</div><p style='color:#94a3b8;font-size:13px;'>Monitoring 250 NSE stocks + 25%/10% step-trailing ladder & 3:45 PM EOD ATR recalibration.</p></div>", unsafe_allow_html=True)

    with b2:
        st.markdown("#### 💼 Portfolio & Live Equity")
        st.markdown(f"""
        <div class="panel-box" style="min-height:250px;">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px;">
                <div>
                    <div style="color:#94a3b8;font-size:12px;">Live Total Equity</div>
                    <div class="metric-big">₹{total_portfolio:,.2f}</div>
                    <div style="color:{'#34d399' if overall_ret_pct >= 0 else '#f87171'};font-size:12px;">{overall_ret_pct:+.2f}% overall</div>
                </div>
                <div>
                    <div style="color:#94a3b8;font-size:12px;">Unrealized P&L</div>
                    <div class="metric-big" style="color:{'#34d399' if unrealized_pnl_total >= 0 else '#f87171'};">₹{unrealized_pnl_total:+,.2f}</div>
                    <div style="color:#94a3b8;font-size:12px;">Realized: ₹{realized_pnl:,.2f}</div>
                </div>
            </div>
            <div style="font-size:12.5px;color:#cbd5e1;line-height:1.9;border-top:1px solid #1e293b;padding-top:10px;">
                <b>Available Cash:</b> ₹{cash:,.2f}<br>
                <b>Invested Value (Live):</b> ₹{current_holdings_val:,.2f}<br>
                <b>Active Positions:</b> {len(open_t)} / 10 max slots
            </div>
        </div>
        """, unsafe_allow_html=True)

    with b3:
        st.markdown("#### 📜 Recent Trade History (IST)")
        if trades_df.empty:
            st.markdown("<div class='panel-box' style='min-height:250px;'><p style='color:#94a3b8;font-size:13px;'>No paper orders executed yet.</p></div>", unsafe_allow_html=True)
        else:
            display_df = trades_df[["timestamp", "symbol", "tier", "qty", "entry", "status", "pnl"]].head(6).copy()
            display_df["timestamp"] = display_df["timestamp"].apply(lambda x: " ".join(str(x).split(" ")[1:]) if len(str(x).split(" ")) > 1 else str(x))
            display_df.columns = ["Time (IST)", "Stock", "Tier", "Qty", "Entry (₹)", "Status", "Realized P&L"]
            st.dataframe(display_df, use_container_width=True, hide_index=True, height=250)

with tab_charts:
    st.markdown("#### 📈 Interactive Live NSE Stock Chart & Bracket Levels")
    portfolio_syms = open_t["symbol"].tolist() if not open_t.empty else []
    default_syms = ["NIFTY 50 (^NSEI)", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]
    selectable_stocks = list(dict.fromkeys(portfolio_syms + default_syms))

    ch1, ch2, ch3 = st.columns([2, 1.5, 1.5])
    with ch1:
        selected_label = st.selectbox("Select Stock (Portfolio holdings first)", selectable_stocks)
        chart_sym = "^NSEI" if "NIFTY 50" in selected_label else selected_label
    with ch2:
        tf_choice = st.selectbox("Timeframe & Candle Interval", [
            "Intraday 5 Days (15m)",
            "Intraday 1 Day (5m)",
            "Swing 1 Month (1h)",
            "Trend 6 Months (1d)",
        ])
    with ch3:
        chart_style = st.radio("Chart Type", ["Candlestick", "Area Line"], horizontal=True)

    tf_map = {
        "Intraday 5 Days (15m)": ("5d", "15m"),
        "Intraday 1 Day (5m)": ("1d", "5m"),
        "Swing 1 Month (1h)": ("1mo", "1h"),
        "Trend 6 Months (1d)": ("6mo", "1d"),
    }
    p_arg, i_arg = tf_map[tf_choice]
    hist_df = get_stock_chart_data(chart_sym, p_arg, i_arg)

    holding_row = None
    if not open_t.empty and chart_sym in open_t["symbol"].values:
        holding_row = open_t[open_t["symbol"] == chart_sym].iloc[0]

    if hist_df.empty:
        st.warning(f"No intraday candle data returned right now for {chart_sym}. Try switching timeframe to 'Trend 6 Months (1d)'.")
    else:
        curr_px = float(hist_df["Close"].iloc[-1])
        prev_px = float(hist_df["Close"].iloc[0])
        chg_pct = ((curr_px - prev_px) / prev_px) * 100

        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric(f"{chart_sym} Live Price", f"₹{curr_px:,.2f}", f"{chg_pct:+.2f}% ({p_arg})")
        if holding_row is not None:
            m_col2.metric("Your Entry Price", f"₹{holding_row['entry']:,.2f}", f"{holding_row['pnl_pct']:+.2f}% P&L")
            m_col3.metric("Stop Loss (Red)", f"₹{holding_row['sl']:,.2f}", f"₹{holding_row['unrealized_pnl']:+,.2f} Unrealized")
            m_col4.metric("Target Price (Green)", f"₹{holding_row['target']:,.2f}", f"Qty: {int(holding_row['qty'])} ({holding_row['tier']})")

        fig = go.Figure()
        if chart_style == "Candlestick":
            fig.add_trace(go.Candlestick(
                x=hist_df.index,
                open=hist_df["Open"],
                high=hist_df["High"],
                low=hist_df["Low"],
                close=hist_df["Close"],
                name=chart_sym,
                increasing_line_color="#10b981",
                decreasing_line_color="#ef4444",
            ))
        else:
            fig.add_trace(go.Scatter(
                x=hist_df.index,
                y=hist_df["Close"],
                mode="lines",
                fill="tozeroy",
                line=dict(color="#38bdf8", width=2.5),
                fillcolor="rgba(56, 189, 248, 0.12)",
                name=chart_sym,
            ))

        if holding_row is not None:
            fig.add_hline(y=float(holding_row["entry"]), line_dash="dash", line_color="#38bdf8", annotation_text=f"Entry ₹{holding_row['entry']:,.2f}")
            fig.add_hline(y=float(holding_row["sl"]), line_dash="dot", line_color="#f87171", annotation_text=f"SL ₹{holding_row['sl']:,.2f}")
            fig.add_hline(y=float(holding_row["target"]), line_dash="dash", line_color="#34d399", annotation_text=f"Target ₹{holding_row['target']:,.2f}")

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b1324",
            plot_bgcolor="#081020",
            height=420,
            margin=dict(l=8, r=8, t=28, b=8),
            xaxis_rangeslider_visible=False,
            yaxis_title="Price (₹ INR)",
        )
        st.plotly_chart(fig, use_container_width=True)

with tab_port:
    st.markdown("#### 💼 Active Virtual NSE Holdings & Live Mark-to-Market P&L")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Available Cash", f"₹{cash:,.2f}")
    p2.metric("Holdings Market Value", f"₹{current_holdings_val:,.2f}")
    p3.metric("Live Unrealized P&L", f"₹{unrealized_pnl_total:+,.2f}")
    p4.metric("Total Account Equity", f"₹{total_portfolio:,.2f}", f"{overall_ret_pct:+.2f}%")
    st.divider()

    if open_t.empty:
        st.info("No open positions held right now.")
    else:
        port_view = open_t[["timestamp", "symbol", "tier", "qty", "entry", "live_ltp", "sl", "target", "unrealized_pnl", "pnl_pct"]].copy()
        port_view.columns = ["Opened (IST)", "Stock", "Tier", "Qty", "Entry (₹)", "Live LTP (₹)", "Stop Loss (₹)", "Target (₹)", "Unrealized P&L (₹)", "Return %"]
        st.dataframe(port_view, use_container_width=True, hide_index=True)

        st.markdown("##### 🛑 Manual Position Exit (Sell at Live Market Price)")
        sell_c1, sell_c2 = st.columns([2, 1])
        with sell_c1:
            sym_to_close = st.selectbox("Select Open Position to Close Manually", open_t["symbol"].tolist())
        with sell_c2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💸 Close Position at Market LTP", use_container_width=True):
                trades_ws = sh.worksheet("trades")
                acct_ws = sh.worksheet("account")
                all_rows = trades_ws.get_all_records()
                for idx, row in enumerate(all_rows, start=2):
                    if row.get("symbol") == sym_to_close and row.get("status") == "OPEN":
                        exit_px = float(live_ltp_map.get(sym_to_close, row["entry"]))
                        qty_val = int(row["qty"])
                        entry_val = float(row["entry"])
                        pnl_val = round((exit_px - entry_val) * qty_val, 2)
                        new_cash = round(cash + exit_px * qty_val, 2)
                        man_id = now_ist().strftime("MAN-%H%M%S-IST")
                        trades_ws.update(f"D{idx}", [["MANUAL_EXIT"]])
                        trades_ws.update(f"I{idx}:J{idx}", [[exit_px, pnl_val]])
                        acct_ws.update("A2", [[new_cash]])
                        sh.worksheet("agent_messages").append_row([
                            man_id,
                            now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
                            "AG5_EXECUTION_BROKER",
                            "PORTFOLIO_LEDGER",
                            "MANUAL_EXIT",
                            f"MANUAL SELL {qty_val} {sym_to_close} @ ₹{exit_px} | Net P&L: ₹{pnl_val}",
                            json.dumps({"symbol": sym_to_close, "exit_price": exit_px, "pnl": pnl_val}),
                        ])
                        ag = infer_trade_agent(row)
                        new_scores = compute_agent_trust_scores(trades_ws.get_all_records())
                        record_trade_reflection(
                            sh,
                            man_id,
                            ag,
                            sym_to_close,
                            str(row.get("tier", "MID")),
                            "MANUAL_EXIT",
                            pnl_val,
                            entry_val,
                            exit_px,
                            trust_scores.get(ag, {}).get("score", 50),
                            new_scores.get(ag, {}).get("score", 50),
                        )
                        break
                st.cache_data.clear()
                st.toast(f"Closed {sym_to_close}!", icon="✅")
                st.rerun()

with tab_agents:
    st.markdown("#### 🏆 Agent Trust Leaderboard (0–100 ELO) & Self-Reflection Log")
    tc1, tc2, tc3 = st.columns(3)
    for col_obj, ag_key in zip([tc1, tc2, tc3], ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]):
        info = trust_scores[ag_key]
        with col_obj:
            st.metric(
                label=info["label"],
                value=f"Trust: {info['score']} / 100",
                delta=f"{info['wins']}W - {info['losses']}L | ₹{info['net_pnl']:+,.2f}",
            )

    st.divider()
    st.markdown("#### 🤖 Individual Agent Decision Inspector & JSON Payloads")
    a_tabs = st.tabs(["🛡️ AG1 (Low)", "📈 AG2 (Mid)", "🚀 AG3 (High)", "🧠 AG4 (Chief)", "⚡ AG5 (Broker)"])
    codes = ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK", "AG4_CHIEF_ANALYST", "AG5_EXECUTION_BROKER"]
    for t_obj, code in zip(a_tabs, codes):
        with t_obj:
            sub = msgs_df[msgs_df["sender"] == code] if not msgs_df.empty else pd.DataFrame()
            if sub.empty:
                st.write("No logs recorded yet.")
            else:
                for _, r in sub.head(10).iterrows():
                    st.markdown(f"**`{r['timestamp']}` ({r['cycle_id']})** — {r['summary']}")
                    with st.expander("Inspect Structured Reasoning JSON"):
                        st.code(r["payload_json"], language="json")

with tab_logs:
    l1, l2 = st.columns(2)
    with l1:
        st.markdown("#### 📜 Complete 30-Day Trade Ledger (IST)")
        st.dataframe(trades_df, use_container_width=True, hide_index=True) if not trades_df.empty else st.write("No trades yet.")
    with l2:
        st.markdown("#### 💬 Inter-Agent Message Stream (IST)")
        for _, r in msgs_df.head(30).iterrows():
            with st.chat_message("assistant"):
                st.markdown(f"**`{r['timestamp']}`** — **{r['sender']}** ➡️ **{r['recipient']}**")
                st.write(r["summary"])

if auto_refresh:
    import time
    time.sleep(60)
    st.cache_data.clear()
    st.rerun()
