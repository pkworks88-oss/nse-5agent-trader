import streamlit as st
from core.ledger import LedgerStore, load_ledger_cached
from core.ui_components import render_desk_workspace, render_market_clock_and_manual_runner, render_top_command_bar

st.set_page_config(page_title="Swing Desk — V4.8", layout="wide")
store = LedgerStore()
state = load_ledger_cached(ttl_seconds=20)
render_market_clock_and_manual_runner("SWING")
render_top_command_bar(ledger_source=state["source"], status_msg=state["status_msg"])
render_desk_workspace(store=store, state=state, desk_key="swing", default_chart_symbol="RELIANCE.NS")
