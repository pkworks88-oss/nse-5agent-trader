import streamlit as st
import sqlite3, asyncio, os
import pandas as pd
from engine import init_db, run_full_cycle, DB_PATH

st.set_page_config(page_title="NSE 5-Agent AI Trading Desk", layout="wide")
init_db()

BADGES = {
    "AG1_LOW_RISK": "🟢 AG1 (Low-Risk Screener)",
    "AG2_MID_RISK": "🟡 AG2 (Mid-Risk Momentum)",
    "AG3_HIGH_RISK": "🔴 AG3 (High-Risk Volatility)",
    "AG4_CHIEF_ANALYST": "🧠 AG4 (Chief Analyst & News)",
    "AG5_EXECUTION_BROKER": "⚡ AG5 (Paper Broker)",
    "PORTFOLIO_LEDGER": "📒 Virtual Ledger",
}

st.sidebar.title("🇮🇳 NSE 5-Agent Cloud Desk")
api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if not api_key:
    api_key = st.sidebar.text_input("Enter Free Gemini API Key", type="password")

if st.sidebar.button("▶️ Run Agent Scan Cycle Now", use_container_width=True):
    if not api_key:
        st.sidebar.error("Please add your free Gemini API key first.")
    else:
        with st.spinner("AG1, AG2, AG3 scanning NSE -> AG4 checking news -> AG5 executing..."):
            asyncio.run(run_full_cycle(api_key))
        st.success("Cycle completed!")

st.title("🤖 5-Agent Autonomous NSE Paper-Trading Desk")
conn = sqlite3.connect(DB_PATH)
cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
trades_df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
msgs_df = pd.read_sql_query("SELECT * FROM agent_messages ORDER BY id DESC LIMIT 100", conn)
conn.close()

open_t = trades_df[trades_df["status"] == "OPEN"] if not trades_df.empty else pd.DataFrame()
closed_t = trades_df[trades_df["status"] != "OPEN"] if not trades_df.empty else pd.DataFrame()
invested = (open_t["qty"] * open_t["entry"]).sum() if not open_t.empty else 0.0
pnl = closed_t["pnl"].sum() if not closed_t.empty else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Virtual Cash Available", f"₹{cash:,.2f}")
c2.metric("Active Capital Invested", f"₹{invested:,.2f}", f"{len(open_t)} Open")
c3.metric("Realized 30-Day P&L", f"₹{pnl:,.2f}")
win_rate = (len(closed_t[closed_t["pnl"] > 0]) / len(closed_t) * 100) if len(closed_t) > 0 else 0.0
c4.metric("Win Rate", f"{win_rate:.1f}%", f"{len(closed_t)} Closed")

tab1, tab2, tab3 = st.tabs(["💬 Live Agent Communication", "📈 Open Positions", "📜 Closed Trade Ledger"])

with tab1:
    if msgs_df.empty:
        st.info("Click **'Run Agent Scan Cycle Now'** in the sidebar to watch AG1–AG5 communicate.")
    for _, row in msgs_df.iterrows():
        with st.chat_message("assistant"):
            st.markdown(f"**`{row['cycle_id']}` | `{row['timestamp']}`** — **{BADGES.get(row['sender'], row['sender'])}** ➡️ **{BADGES.get(row['recipient'], row['recipient'])}**")
            st.write(row["summary"])
            with st.expander("View Raw JSON Handoff"):
                st.code(row["payload_json"], language="json")

with tab2:
    st.dataframe(open_t, use_container_width=True) if not open_t.empty else st.write("No open virtual positions.")

with tab3:
    st.dataframe(closed_t, use_container_width=True) if not closed_t.empty else st.write("No closed trades yet.")
