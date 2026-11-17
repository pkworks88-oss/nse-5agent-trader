import streamlit as st
import sqlite3
import asyncio
import os
import pandas as pd
from google.genai.errors import ClientError
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

secret_key = ""
try:
    secret_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    secret_key = ""

api_key = secret_key or os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("Enter Free Gemini API Key", type="password")

if st.sidebar.button("▶️ Run Agent Scan Cycle Now", use_container_width=True):
    if not api_key:
        st.sidebar.error("Please add your free Gemini API key in Secrets or the input box above.")
    else:
        try:
            with st.spinner("AG1, AG2, AG3 scanning NSE -> AG4 checking news -> AG5 executing..."):
                asyncio.run(run_full_cycle(api_key))
            st.success("Cycle completed!")
        except ClientError as e:
            st.error(f"Gemini API Error ({e.code}): {e.message}. Check your API key in Streamlit Secrets.")
        except Exception as e:
            st.error(f"Unexpected Error: {e}")

selected_agents = st.sidebar.multiselect(
    "Filter Agent Comms",
    options=list(BADGES.keys()),
    default=list(BADGES.keys()),
    format_func=lambda x: BADGES[x],
)

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

st.divider()

tab1, tab2, tab3, tab4 = st.tabs([
    "💬 Live Agent Communication",
    "📈 Open Positions",
    "📜 Closed Trade Ledger",
    "📊 AG1 vs AG2 vs AG3 Performance",
])

with tab1:
    if msgs_df.empty:
        st.info("Click **'Run Agent Scan Cycle Now'** in the sidebar to watch AG1–AG5 communicate.")
    else:
        filtered = msgs_df[msgs_df["sender"].isin(selected_agents)]
        for _, row in filtered.iterrows():
            with st.chat_message("assistant"):
                st.markdown(
                    f"**`{row['cycle_id']}` | `{row['timestamp']}`** — "
                    f"**{BADGES.get(row['sender'], row['sender'])}** ➡️ **{BADGES.get(row['recipient'], row['recipient'])}**"
                )
                st.write(row["summary"])
                with st.expander("View Raw JSON Handoff"):
                    st.code(row["payload_json"], language="json")

with tab2:
    st.dataframe(open_t, use_container_width=True) if not open_t.empty else st.write("No open virtual positions.")

with tab3:
    st.dataframe(closed_t, use_container_width=True) if not closed_t.empty else st.write("No closed trades yet.")

with tab4:
    if not closed_t.empty:
        summary = closed_t.groupby("tier").agg(
            Total_Trades=("id", "count"),
            Net_PnL_INR=("pnl", "sum"),
            Avg_PnL_INR=("pnl", "mean"),
        ).reset_index()
        st.dataframe(summary, use_container_width=True)
    else:
        st.write("Complete at least one paper trade to compare AG1 (Low), AG2 (Mid), and AG3 (High) performance.")
