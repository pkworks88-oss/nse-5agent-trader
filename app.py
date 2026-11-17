import streamlit as st
import sqlite3
import asyncio
import os
import json
from datetime import datetime
import pandas as pd
import yfinance as yf
from engine import init_db, run_full_cycle, DB_PATH

st.set_page_config(
    page_title="5-Agent Intelligent Stock Trading Platform",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()

# --- CUSTOM CYBER-TERMINAL CSS (MATCHING REFERENCE MOCKUPS) ---
st.markdown("""
<style>
    /* Main Dark Navy Background */
    .stApp {
        background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%);
        color: #e2e8f0;
    }
    section[data-testid="stSidebar"] {
        background-color: #081020;
        border-right: 1px solid #1e293b;
    }
    /* Top Header Banner */
    .top-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(90deg, #0d172a 0%, #111f38 100%);
        border: 1px solid #1e3a5f;
        border-radius: 12px;
        padding: 12px 20px;
        margin-bottom: 16px;
    }
    .platform-title {
        font-size: 22px;
        font-weight: 700;
        color: #f8fafc;
        margin: 0;
    }
    .live-pill {
        background: rgba(16, 185, 129, 0.15);
        border: 1px solid #10b981;
        color: #34d399;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 600;
    }
    /* Glowing Agent Workflow Cards */
    .wf-card {
        border-radius: 12px;
        padding: 14px 10px;
        text-align: center;
        background: #0b1426;
        min-height: 135px;
    }
    .wf-green { border: 1.5px solid #10b981; box-shadow: 0 0 15px rgba(16, 185, 129, 0.25); }
    .wf-blue  { border: 1.5px solid #3b82f6; box-shadow: 0 0 15px rgba(59, 130, 246, 0.25); }
    .wf-orange{ border: 1.5px solid #f97316; box-shadow: 0 0 15px rgba(249, 115, 22, 0.25); }
    .wf-purple{ border: 1.5px solid #a855f7; box-shadow: 0 0 15px rgba(168, 85, 247, 0.25); }
    .wf-teal  { border: 1.5px solid #06b6d4; box-shadow: 0 0 15px rgba(6, 182, 212, 0.25); }
    .wf-title { font-size: 18px; font-weight: 800; color: #ffffff; margin: 4px 0; }
    .wf-sub   { font-size: 12px; color: #94a3b8; margin-bottom: 8px; }
    .wf-stat  { font-size: 12px; font-weight: 600; color: #34d399; }

    /* Agent Column Cards */
    .agent-col {
        background: #0b1324;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 12px;
        min-height: 290px;
    }
    .chat-bubble {
        background: #131f38;
        border-left: 3px solid #38bdf8;
        border-radius: 8px;
        padding: 8px 10px;
        margin-bottom: 8px;
        font-size: 12.5px;
        color: #e2e8f0;
    }
    .chat-time {
        font-size: 10.5px;
        color: #64748b;
        display: block;
        margin-bottom: 2px;
    }
    /* Bottom Cards */
    .panel-box {
        background: #0b1324;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 16px;
        min-height: 250px;
    }
    .rec-ticker {
        font-size: 24px;
        font-weight: 800;
        color: #34d399;
        letter-spacing: 0.5px;
    }
    .metric-big {
        font-size: 24px;
        font-weight: 800;
        color: #f8fafc;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=120)
def get_indian_indices():
    indices = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "BANK NIFTY": "^NSEBANK"}
    out = {}
    for name, sym in indices.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                curr = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                diff = curr - prev
                pct = (diff / prev) * 100
                out[name] = (curr, diff, pct, hist["Close"].tolist())
            else:
                out[name] = (24797.25, 182.75, 0.74, [24600, 24700, 24797])
        except Exception:
            out[name] = (24797.25, 182.75, 0.74, [24600, 24700, 24797])
    return out


# --- SIDEBAR NAVIGATION (MATCHING LEFT SIDEBAR IN SAMPLE) ---
st.sidebar.markdown("## 📈 AI Stock Trader\n<span style='color:#94a3b8;font-size:12px;'>Multi-Agent Trading System</span>", unsafe_allow_html=True)
st.sidebar.divider()

nav = st.sidebar.radio(
    "Navigation",
    ["🏠 Live Dashboard", "🤖 Agent Monitor", "💬 Communication Log", "💼 Portfolio", "📜 Trade History"],
    label_visibility="collapsed",
)

st.sidebar.divider()
secret_key = ""
try:
    secret_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    secret_key = ""

api_key = secret_key or os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

if st.sidebar.button("⚡ Run 5-Agent Cycle Now", use_container_width=True, type="primary"):
    if not api_key:
        st.sidebar.error("Add GEMINI_API_KEY in Secrets first.")
    else:
        try:
            with st.spinner("AG1, AG2, AG3 scanning NSE → AG4 analyzing → AG5 executing..."):
                asyncio.run(run_full_cycle(api_key))
            st.toast("5-Agent Trading Cycle Completed!", icon="✅")
        except Exception as e:
            st.error(f"Cycle Notice: {e}")

st.sidebar.markdown(
    f"<div style='margin-top:24px;padding:10px;background:#0d1a2f;border-radius:8px;border:1px solid #1e3a5f;'>"
    f"<span style='color:#34d399;font-weight:700;'>● System Online</span><br>"
    f"<span style='color:#94a3b8;font-size:12px;'>{datetime.now().strftime('%H:%M:%S')} (Server Time)</span>"
    f"</div>",
    unsafe_allow_html=True,
)

# --- LOAD DATABASE STATE ---
conn = sqlite3.connect(DB_PATH)
cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
trades_df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
msgs_df = pd.read_sql_query("SELECT * FROM agent_messages ORDER BY id DESC LIMIT 120", conn)
conn.close()

open_t = trades_df[trades_df["status"] == "OPEN"] if not trades_df.empty else pd.DataFrame()
closed_t = trades_df[trades_df["status"] != "OPEN"] if not trades_df.empty else pd.DataFrame()
invested = (open_t["qty"] * open_t["entry"]).sum() if not open_t.empty else 0.0
total_portfolio = cash + invested
realized_pnl = closed_t["pnl"].sum() if not closed_t.empty else 0.0
overall_ret_pct = ((total_portfolio - 1_000_000.0) / 1_000_000.0) * 100

indices = get_indian_indices()
nifty_val, nifty_diff, nifty_pct, nifty_series = indices["NIFTY 50"]
sensex_val, sensex_diff, sensex_pct, _ = indices["SENSEX"]
bnifty_val, bnifty_diff, bnifty_pct, _ = indices["BANK NIFTY"]

# --- TOP COMMAND BAR ---
st.markdown(f"""
<div class="top-bar">
    <div>
        <span class="platform-title">5-Agent Intelligent Stock Trading Platform</span>
        <span style="margin-left:16px;color:#94a3b8;font-size:13px;">
            NSE • <b>NIFTY 50</b> {nifty_val:,.2f} <span style="color:#34d399;">({nifty_pct:+.2f}%)</span>  | 
            <b>SENSEX</b> {sensex_val:,.2f} <span style="color:#34d399;">({sensex_pct:+.2f}%)</span>
        </span>
    </div>
    <div>
        <span class="live-pill">● Live NSE Paper Market</span>
    </div>
</div>
""", unsafe_allow_html=True)

# Helper to get latest agent messages
def get_agent_msgs(sender_code: str, limit: int = 3):
    if msgs_df.empty:
        return []
    sub = msgs_df[msgs_df["sender"] == sender_code].head(limit)
    return sub.to_dict("records")

ag1_msgs = get_agent_msgs("AG1_LOW_RISK", 3)
ag2_msgs = get_agent_msgs("AG2_MID_RISK", 3)
ag3_msgs = get_agent_msgs("AG3_HIGH_RISK", 3)
ag4_msgs = get_agent_msgs("AG4_CHIEF_ANALYST", 3)
ag5_msgs = get_agent_msgs("AG5_EXECUTION_BROKER", 3)

# ==============================================================================
# VIEW 1: LIVE DASHBOARD (EXACT COMMAND CENTER LAYOUT)
# ==============================================================================
if nav == "🏠 Live Dashboard":
    top_left, top_right = st.columns([3.3, 1.3])

    with top_left:
        st.markdown("#### ⚡ 5-Agent Workflow & Communication Flow")
        w1, a1, w2, a2, w3, a3, w4, a4, w5 = st.columns([1.8, 0.3, 1.8, 0.3, 1.8, 0.3, 2.0, 0.3, 1.8])

        with w1:
            st.markdown(f"""
            <div class="wf-card wf-green">
                <div style="font-size:22px;">🛡️</div>
                <div class="wf-title">AG1</div>
                <div class="wf-sub">Low Risk Analyst</div>
                <div class="wf-stat">✓ {'Active' if ag1_msgs else 'Standby'}</div>
            </div>""", unsafe_allow_html=True)
        with a1:
            st.markdown("<div style='text-align:center;padding-top:55px;font-size:22px;color:#64748b;'>→</div>", unsafe_allow_html=True)
        with w2:
            st.markdown(f"""
            <div class="wf-card wf-blue">
                <div style="font-size:22px;">📈</div>
                <div class="wf-title">AG2</div>
                <div class="wf-sub">Mid Risk Analyst</div>
                <div class="wf-stat">✓ {'Active' if ag2_msgs else 'Standby'}</div>
            </div>""", unsafe_allow_html=True)
        with a2:
            st.markdown("<div style='text-align:center;padding-top:55px;font-size:22px;color:#64748b;'>→</div>", unsafe_allow_html=True)
        with w3:
            st.markdown(f"""
            <div class="wf-card wf-orange">
                <div style="font-size:22px;">🚀</div>
                <div class="wf-title">AG3</div>
                <div class="wf-sub">High Risk Analyst</div>
                <div class="wf-stat">✓ {'Active' if ag3_msgs else 'Standby'}</div>
            </div>""", unsafe_allow_html=True)
        with a3:
            st.markdown("<div style='text-align:center;padding-top:55px;font-size:22px;color:#a855f7;'>⇉</div>", unsafe_allow_html=True)
        with w4:
            st.markdown(f"""
            <div class="wf-card wf-purple">
                <div style="font-size:22px;">🧠</div>
                <div class="wf-title">AG4</div>
                <div class="wf-sub">Final Analysis & Selection</div>
                <div class="wf-stat">✓ {'Verified' if ag4_msgs else 'Waiting'}</div>
            </div>""", unsafe_allow_html=True)
        with a4:
            st.markdown("<div style='text-align:center;padding-top:55px;font-size:22px;color:#06b6d4;'>→</div>", unsafe_allow_html=True)
        with w5:
            st.markdown(f"""
            <div class="wf-card wf-teal">
                <div style="font-size:22px;">🛒</div>
                <div class="wf-title">AG5</div>
                <div class="wf-sub">Execute Trades</div>
                <div class="wf-stat">✓ {len(open_t)} Open</div>
            </div>""", unsafe_allow_html=True)

    with top_right:
        st.markdown("#### 📊 Live Market Snapshot")
        st.markdown(f"""
        <div class="panel-box" style="min-height:135px;padding:12px;">
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
                <span style="color:#94a3b8;">NIFTY 50</span>
                <span><b>{nifty_val:,.2f}</b> <span style="color:#34d399;">{nifty_pct:+.2f}%</span></span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
                <span style="color:#94a3b8;">SENSEX</span>
                <span><b>{sensex_val:,.2f}</b> <span style="color:#34d399;">{sensex_pct:+.2f}%</span></span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:13px;">
                <span style="color:#94a3b8;">BANK NIFTY</span>
                <span><b>{bnifty_val:,.2f}</b> <span style="color:#34d399;">{bnifty_pct:+.2f}%</span></span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # --- MIDDLE ROW: 5 PARALLEL AGENT COMMUNICATION COLUMNS ---
    st.markdown("#### 💬 Agent Communication — Live Chat Stream")
    c_ag1, c_ag2, c_ag3, c_ag4, c_ag5 = st.columns(5)

    def render_agent_column(col, title, border_color, records, empty_hint):
        with col:
            st.markdown(
                f"<div class='agent-col' style='border-top: 3px solid {border_color};'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>"
                f"<b style='font-size:13.5px;color:#f8fafc;'>{title}</b>"
                f"<span style='color:#34d399;font-size:11px;'>● Online</span></div>",
                unsafe_allow_html=True,
            )
            if not records:
                st.markdown(f"<div class='chat-bubble' style='border-left-color:{border_color};'>{empty_hint}</div>", unsafe_allow_html=True)
            else:
                for r in records:
                    ts = r["timestamp"].split(" ")[-1]
                    st.markdown(
                        f"<div class='chat-bubble' style='border-left-color:{border_color};'>"
                        f"<span class='chat-time'>🤖 {ts} • {r['cycle_id']}</span>"
                        f"{r['summary']}</div>",
                        unsafe_allow_html=True,
                    )
            st.markdown("</div>", unsafe_allow_html=True)

    render_agent_column(c_ag1, "🛡️ AG1 – Low Risk", "#10b981", ag1_msgs, "Scanning low-beta NIFTY large caps...")
    render_agent_column(c_ag2, "📈 AG2 – Mid Risk", "#3b82f6", ag2_msgs, "Scanning momentum & RVOL breakouts...")
    render_agent_column(c_ag3, "🚀 AG3 – High Risk", "#f97316", ag3_msgs, "Scanning high-beta intraday movers...")
    render_agent_column(c_ag4, "🧠 AG4 – Chief Analyst", "#a855f7", ag4_msgs, "Awaiting proposals + news sentiment audit...")
    render_agent_column(c_ag5, "⚡ AG5 – Executor", "#06b6d4", ag5_msgs, "Monitoring bracket orders & SL/TP...")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- BOTTOM ROW: FINAL RECOMMENDATION | PORTFOLIO PERFORMANCE | RECENT TRADE HISTORY ---
    b1, b2, b3 = st.columns([1.2, 1.3, 1.5])

    with b1:
        st.markdown("#### 🏆 Final Recommendation (AG4)")
        latest_buy = None
        if ag4_msgs:
            try:
                payload = json.loads(ag4_msgs[0]["payload_json"])
                trades_list = payload.get("selected_trades", [])
                buys = [t for t in trades_list if t.get("action") == "BUY"]
                if buys:
                    latest_buy = buys[0]
            except Exception:
                pass

        if latest_buy:
            rr_val = round(
                (latest_buy["target_price"] - latest_buy["entry_price"])
                / max(latest_buy["entry_price"] - latest_buy["stop_loss"], 0.01),
                1,
            )
            st.markdown(f"""
            <div class="panel-box">
                <div class="rec-ticker">{latest_buy['symbol']}</div>
                <div style="color:#94a3b8;font-size:12px;margin-bottom:10px;">Tier: <b>{latest_buy['risk_tier']}</b> • Conviction: <b>{latest_buy['conviction']}%</b></div>
                <div style="font-size:13px;line-height:1.8;">
                    <b>Entry Price:</b> ₹{latest_buy['entry_price']:,.2f}<br>
                    <b>Stop Loss:</b> <span style="color:#f87171;">₹{latest_buy['stop_loss']:,.2f}</span><br>
                    <b>Target Price:</b> <span style="color:#34d399;">₹{latest_buy['target_price']:,.2f}</span><br>
                    <b>Risk / Reward:</b> 1 : {rr_val}
                </div>
                <div style="margin-top:10px;padding:8px;background:#131f38;border-radius:8px;font-size:12px;color:#cbd5e1;">
                    ✓ {latest_buy['reasoning']}
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="panel-box">
                <div class="rec-ticker">READY TO SCAN</div>
                <p style="color:#94a3b8;font-size:13px;">Click <b>⚡ Run 5-Agent Cycle Now</b> in the left sidebar to generate AG4's highest-conviction NSE stock pick.</p>
            </div>
            """, unsafe_allow_html=True)

    with b2:
        st.markdown("#### 💼 Portfolio & Performance")
        st.markdown(f"""
        <div class="panel-box">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px;">
                <div>
                    <div style="color:#94a3b8;font-size:12px;">Total Portfolio Value</div>
                    <div class="metric-big">₹{total_portfolio:,.2f}</div>
                    <div style="color:#34d399;font-size:12px;">{overall_ret_pct:+.2f}% overall</div>
                </div>
                <div>
                    <div style="color:#94a3b8;font-size:12px;">Realized P&L</div>
                    <div class="metric-big" style="color:{'#34d399' if realized_pnl >= 0 else '#f87171'};">₹{realized_pnl:,.2f}</div>
                    <div style="color:#94a3b8;font-size:12px;">Cash: ₹{cash:,.0f}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)
        chart_data = pd.DataFrame({"NIFTY 50 Intraday Trend": nifty_series})
        st.line_chart(chart_data, height=130)
        st.markdown("</div>", unsafe_allow_html=True)

    with b3:
        st.markdown("#### 📜 Recent Trade History")
        if trades_df.empty:
            st.markdown("""
            <div class="panel-box">
                <p style="color:#94a3b8;font-size:13px;">No paper orders executed yet. When AG4 approves a BUY signal, AG5 will automatically size the position and log it here.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            display_df = trades_df[["timestamp", "symbol", "tier", "qty", "entry", "status", "pnl"]].head(6).copy()
            display_df["timestamp"] = display_df["timestamp"].apply(lambda x: x.split(" ")[-1])
            display_df.columns = ["Time", "Stock", "Tier", "Qty", "Price (₹)", "Status", "P&L (₹)"]
            st.dataframe(display_df, use_container_width=True, hide_index=True, height=250)

# ==============================================================================
# VIEW 2: AGENT MONITOR (DETAILED ROLE & OUTPUT INSPECTOR)
# ==============================================================================
elif nav == "🤖 Agent Monitor":
    st.subheader("🤖 Individual Agent Roles & Latest Outputs")
    selected_tab = st.tabs(["AG1 (Low Risk)", "AG2 (Mid Risk)", "AG3 (High Risk)", "AG4 (Chief Analyst)", "AG5 (Executor)"])
    codes = ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK", "AG4_CHIEF_ANALYST", "AG5_EXECUTION_BROKER"]
    descriptions = [
        "Scans liquid NSE large-caps with low Beta, healthy trend above 200-SMA, and tight ATR stop-losses.",
        "Scans mid-to-large growth stocks with strong relative volume (RVOL) and momentum breakouts.",
        "Scans high-beta, high-volatility intraday movers with strict 1x ATR invalidation levels.",
        "Audits all proposals from AG1–AG3 against live Google News RSS headlines and technical risk/reward ratios.",
        "Calculates tier-adjusted position size on ₹10L virtual capital and executes bracket BUY/SELL orders.",
    ]
    for tab, code, desc in zip(selected_tab, codes, descriptions):
        with tab:
            st.info(f"**Role & Responsibilities:** {desc}")
            sub = msgs_df[msgs_df["sender"] == code] if not msgs_df.empty else pd.DataFrame()
            if sub.empty:
                st.write("No logs recorded for this agent yet.")
            else:
                for _, r in sub.head(10).iterrows():
                    st.markdown(f"**`{r['timestamp']}` ({r['cycle_id']})** — {r['summary']}")
                    with st.expander("Inspect Structured JSON"):
                        st.code(r["payload_json"], language="json")

# ==============================================================================
# VIEW 3: FULL COMMUNICATION LOG
# ==============================================================================
elif nav == "💬 Communication Log":
    st.subheader("💬 Complete Inter-Agent Audit Log")
    if msgs_df.empty:
        st.info("No inter-agent messages logged yet.")
    else:
        for _, r in msgs_df.iterrows():
            with st.chat_message("assistant"):
                st.markdown(f"**`{r['cycle_id']}` | `{r['timestamp']}`** — **{r['sender']}** ➡️ **{r['recipient']}**")
                st.write(r["summary"])
                with st.expander("Raw Payload"):
                    st.code(r["payload_json"], language="json")

# ==============================================================================
# VIEW 4: PORTFOLIO
# ==============================================================================
elif nav == "💼 Portfolio":
    st.subheader("💼 Active Virtual NSE Holdings")
    c1, c2, c3 = st.columns(3)
    c1.metric("Available Cash", f"₹{cash:,.2f}")
    c2.metric("Invested Capital", f"₹{invested:,.2f}")
    c3.metric("Total Account Equity", f"₹{total_portfolio:,.2f}", f"{overall_ret_pct:+.2f}%")
    st.divider()
    st.dataframe(open_t, use_container_width=True, hide_index=True) if not open_t.empty else st.write("No open positions currently held.")

# ==============================================================================
# VIEW 5: TRADE HISTORY
# ==============================================================================
elif nav == "📜 Trade History":
    st.subheader("📜 Complete 30-Day Demo Trade Ledger")
    st.dataframe(trades_df, use_container_width=True, hide_index=True) if not trades_df.empty else st.write("No trades recorded yet.")
