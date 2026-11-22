import json
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

from core.attribution import compute_desk_and_agent_trust
from core.config import DEFAULT_DESKS
from core.ledger import LedgerStore
from core.ui_components import (
    render_agent_war_room,
    render_desk_and_agent_scorecards,
    render_filter_watch_table,
    render_top_command_bar,
)

st.set_page_config(
    page_title="NSE Multi-Asset AI Trading Platform — V4.8",
    page_icon="📈",
    layout="wide",
)

if "desk_budgets" not in st.session_state:
    st.session_state.desk_budgets = {k: v.capital for k, v in DEFAULT_DESKS.items()}
if "soft_cushion_pct" not in st.session_state:
    st.session_state.soft_cushion_pct = 10.0
if "baseline_slots" not in st.session_state:
    st.session_state.baseline_slots = 15

store = LedgerStore()
state = store.load_all()
render_top_command_bar(ledger_source=state["source"], status_msg=state["status_msg"])

accounts = state.get("accounts", {})
all_trades = state["trades"]
open_trades = [t for t in all_trades if t["status"] == "OPEN"]
closed_trades = [t for t in all_trades if t["status"] != "OPEN"]
trust_data = compute_desk_and_agent_trust(all_trades)

# User-configured multi-desk capital controls (initialised to 10L Swing, 1L Intraday, 1L F&O, 0 Commodity)
with st.expander("Multi-Desk User-Configured Capital & Soft Cushion Settings", expanded=False):
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    st.session_state.desk_budgets["swing"] = c1.number_input("Swing Capital (₹)", min_value=0.0, step=10000.0, value=float(st.session_state.desk_budgets["swing"]))
    st.session_state.desk_budgets["intraday"] = c2.number_input("Intraday Capital (₹)", min_value=0.0, step=10000.0, value=float(st.session_state.desk_budgets["intraday"]))
    st.session_state.desk_budgets["fno"] = c3.number_input("F&O Capital (₹)", min_value=0.0, step=10000.0, value=float(st.session_state.desk_budgets["fno"]))
    st.session_state.desk_budgets["commodity"] = c4.number_input("Commodity Capital (₹)", min_value=0.0, step=10000.0, value=float(st.session_state.desk_budgets["commodity"]))
    st.session_state.soft_cushion_pct = c5.number_input("Soft Cushion (%)", min_value=0.0, max_value=40.0, step=1.0, value=float(st.session_state.soft_cushion_pct))
    st.session_state.baseline_slots = int(c6.number_input("Baseline Slots", min_value=1, max_value=30, step=1, value=int(st.session_state.baseline_slots)))

total_configured_cap = sum(st.session_state.desk_budgets.values())
swing_free_cash = float(accounts.get("swing_cash", 41210.02))
eq_free_cash = float(accounts.get("eq_cash", 102804.73))
fno_free_cash = float(accounts.get("fno_cash", 110676.95))
commodity_free_cash = float(st.session_state.desk_budgets["commodity"])
total_free_cash = swing_free_cash + eq_free_cash + fno_free_cash + commodity_free_cash

unrealized_net = sum(float(t.get("mtm_pnl", 0.0)) for t in open_trades)
realized_net = sum(float(t.get("pnl", 0.0)) for t in closed_trades)
total_net_pnl = unrealized_net + realized_net

k1, k2, k3, k4 = st.columns(4)
k1.markdown(
    f"<div class='v48-kpi'>"
    f"<div class='v48-kpi-label'>Total Capital &amp; Combined Free Cash</div>"
    f"<div class='v48-kpi-val'>₹{total_configured_cap:,.0f}</div>"
    f"<div class='v48-kpi-sub'>Free Cash: ₹{total_free_cash:,.2f} (SW ₹{swing_free_cash:,.0f} | EQ ₹{eq_free_cash:,.0f} | F&O ₹{fno_free_cash:,.0f})</div>"
    f"</div>",
    unsafe_allow_html=True,
)
overflow_cnt = max(0, len(open_trades) - st.session_state.baseline_slots)
k2.markdown(
    f"<div class='v48-kpi'>"
    f"<div class='v48-kpi-label'>Active Open Positions &amp; Cushion</div>"
    f"<div class='v48-kpi-val'>{len(open_trades)} / {st.session_state.baseline_slots} Baseline</div>"
    f"<div class='v48-kpi-sub'>{overflow_cnt} Overflow | Closed Ledger Trades: {len(closed_trades)}</div>"
    f"</div>",
    unsafe_allow_html=True,
)
pnl_col = "#34D399" if total_net_pnl >= 0 else "#FB7185"
k3.markdown(
    f"<div class='v48-kpi'>"
    f"<div class='v48-kpi-label'>Total Net P&amp;L (Unreal + Realized)</div>"
    f"<div class='v48-kpi-val' style='color:{pnl_col};'>{'+' if total_net_pnl >= 0 else ''}₹{total_net_pnl:,.2f}</div>"
    f"<div class='v48-kpi-sub'>Unreal: ₹{unrealized_net:+,.2f} | Realized (Both Books): ₹{realized_net:+,.2f}</div>"
    f"</div>",
    unsafe_allow_html=True,
)
k4.markdown(
    f"<div class='v48-kpi'>"
    f"<div class='v48-kpi-label'>Composite Trust &amp; Graduation Gate</div>"
    f"<div class='v48-kpi-val'>{trust_data['composite_trust']}/100 (PF {trust_data['profit_factor']:.2f})</div>"
    f"<div class='v48-kpi-sub'>Closed {trust_data['total_closed_count']}/100 | Live v4.7.2 Open: {len(open_trades)}</div>"
    f"</div>",
    unsafe_allow_html=True,
)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

# Filter Watch Table (All 20 Live Open Positions with TIER, PROG %, PROTECTION & MTM P&L)
render_filter_watch_table(
    open_trades=open_trades,
    key_prefix="overview",
    on_close_callback=lambda sym: store.close_paper_position(sym),
)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

# 4-Desk (Swing, Intraday, F&O, Commodity) & 5-Agent Circular Trust Scorecards
render_desk_and_agent_scorecards(trust_data)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

ex1, ex2, ex3 = st.columns(3)
df_all = pd.DataFrame(all_trades)
ex1.download_button(
    "⬇ Download Combined Ledger CSV (Both Books)",
    data=df_all.to_csv(index=False).encode("utf-8"),
    file_name=f"nse_v4_8_combined_ledger_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
    mime="text/csv",
)
ex2.download_button(
    "⬇ Download Open Watch Table CSV",
    data=pd.DataFrame(open_trades).to_csv(index=False).encode("utf-8"),
    file_name=f"nse_v4_8_open_watch_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
    mime="text/csv",
)
ex3.download_button(
    "⬇ Download Complete V4.8 JSON Snapshot",
    data=json.dumps({"schema": "v4.8", "live_mode": False, "accounts": accounts, "trust": trust_data, "trades": all_trades}, indent=2, default=str).encode("utf-8"),
    file_name=f"nse_v4_8_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json",
    mime="application/json",
)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
render_agent_war_room(state["agent_messages"], limit=10)
