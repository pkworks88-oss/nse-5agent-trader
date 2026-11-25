import pandas as pd
import streamlit as st
from core.attribution import compute_desk_and_agent_trust
from core.ledger import LedgerStore, load_ledger_cached
from core.ui_components import render_agent_war_room, render_desk_and_agent_scorecards, render_top_command_bar

st.set_page_config(page_title="Post-Trade Attribution & Ledger — V4.8", layout="wide")
store = LedgerStore()
state = load_ledger_cached(ttl_seconds=20)
render_top_command_bar(ledger_source=state["source"], status_msg=state["status_msg"])

all_trades = state["trades"]
trust_data = compute_desk_and_agent_trust(all_trades)

st.header("Post-Trade Attribution, Graduation Scorecard & Combined Ledger")
m1, m2, m3, m4 = st.columns(4)
m1.metric("V4.8 Closed Trades (Graduation)", f"{trust_data['v48_closed_count']} / 100", f"Total Closed (All Versions): {trust_data['total_closed_count']}")
m2.metric("Profit Factor (Post-Cost)", f"{trust_data['profit_factor']:.2f}", "Target >= 1.50")
m3.metric("Weighted Composite Trust", f"{trust_data['composite_trust']}/100", f"Avg MFE {trust_data['avg_mfe_pct']:+.2f}% / MAE {trust_data['avg_mae_pct']:+.2f}%")
m4.metric("Live Graduation Gate", "READY" if trust_data["graduation_ready"] else "PAPER LOCKED")

render_desk_and_agent_scorecards(trust_data)

st.subheader("Complete Combined Trade Ledger (single V4.8 workbook)")
df = pd.DataFrame(all_trades)
if not df.empty:
    f1, f2, f3 = st.columns(3)
    book_f = f1.selectbox("Filter Workbook", ["ALL"] + sorted({str(x) for x in df["ledger_book"].dropna().unique()}) if "ledger_book" in df.columns else ["ALL"])
    desk_f = f2.selectbox("Filter Desk", ["ALL", "Swing", "Intraday", "F&O", "Commodity"])
    status_f = f3.selectbox("Filter Status", ["ALL", "OPEN", "CLOSED"])
    if book_f != "ALL":
        df = df[df["ledger_book"] == book_f]
    if desk_f != "ALL":
        df = df[df["desk"] == desk_f]
    if status_f == "OPEN":
        df = df[df["status"] == "OPEN"]
    elif status_f == "CLOSED":
        df = df[df["status"] != "OPEN"]
    st.dataframe(df, width="stretch", hide_index=True)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
render_agent_war_room(state.get("agent_messages", []), limit=15, scope_desk="POSTTRADE")
