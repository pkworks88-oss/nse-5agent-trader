import json
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st

from core.attribution import compute_desk_and_agent_trust
from core.config import DEFAULT_DESKS, get_effective_desk_config
from core.ledger import LedgerStore, load_ledger_cached
from core.ui_components import (
    derive_free_cash,
    render_agent_war_room,
    render_capital_settings,
    render_desk_and_agent_scorecards,
    render_filter_watch_table,
    render_market_clock_and_manual_runner,
    render_top_command_bar,
)

st.set_page_config(page_title="NSE Multi-Asset AI Trading Platform — V4.8", page_icon="📈", layout="wide")

store = LedgerStore()
state = load_ledger_cached(ttl_seconds=20)
render_market_clock_and_manual_runner("ALL")
render_top_command_bar(ledger_source=state["source"], status_msg=state["status_msg"])

accounts = state.get("accounts", {})
all_trades = state["trades"]
open_trades = [t for t in all_trades if t["status"] == "OPEN"]
closed_trades = [t for t in all_trades if t["status"] != "OPEN"]
trust_data = compute_desk_and_agent_trust(all_trades)

with st.expander("Multi-Desk Capital, Risk & Slot Settings (saved to the runner)", expanded=False):
    render_capital_settings(store, accounts, key_prefix="dash")

eff_cfgs = {k: get_effective_desk_config(k, accounts) for k in DEFAULT_DESKS}
total_configured_cap = sum(c.capital for c in eff_cfgs.values())
swing_free_cash = derive_free_cash(eff_cfgs["swing"], all_trades)
eq_free_cash = derive_free_cash(eff_cfgs["intraday"], all_trades)
fno_free_cash = derive_free_cash(eff_cfgs["fno"], all_trades)
comm_free_cash = derive_free_cash(eff_cfgs["commodity"], all_trades)
total_free_cash = swing_free_cash + eq_free_cash + fno_free_cash + comm_free_cash
total_baseline_slots = sum(c.baseline_slots for c in eff_cfgs.values() if c.capital > 0)

unrealized_gross = sum(float(t.get("mtm_gross", t.get("mtm_pnl", 0.0))) for t in open_trades)
unrealized_net = sum(float(t.get("mtm_pnl", 0.0)) for t in open_trades)
realized_net = sum(float(t.get("pnl", 0.0)) for t in closed_trades)
total_net_pnl = unrealized_net + realized_net

k1, k2, k3, k4 = st.columns(4)
k1.markdown(
    f"<div class='v48-kpi'><div class='v48-kpi-label'>Total Capital &amp; Combined Free Cash</div>"
    f"<div class='v48-kpi-val'>₹{total_configured_cap:,.0f}</div>"
    f"<div class='v48-kpi-sub'>Free Cash: ₹{total_free_cash:,.2f} (SW ₹{swing_free_cash:,.0f} | EQ ₹{eq_free_cash:,.0f} | F&O ₹{fno_free_cash:,.0f})</div></div>",
    unsafe_allow_html=True,
)
overflow_cnt = max(0, len(open_trades) - total_baseline_slots)
k2.markdown(
    f"<div class='v48-kpi'><div class='v48-kpi-label'>Active Open Positions (All Desks)</div>"
    f"<div class='v48-kpi-val'>{len(open_trades)} / {total_baseline_slots} Multi-Desk Slots</div>"
    f"<div class='v48-kpi-sub'>{overflow_cnt} Overflow | Closed: {len(closed_trades)} (V4.8: {trust_data['v48_closed_count']})</div></div>",
    unsafe_allow_html=True,
)
pnl_col = "#34D399" if total_net_pnl >= 0 else "#FB7185"
k3.markdown(
    f"<div class='v48-kpi'><div class='v48-kpi-label'>Total Net P&amp;L (Unreal Net + Realized)</div>"
    f"<div class='v48-kpi-val' style='color:{pnl_col};'>{'+' if total_net_pnl >= 0 else ''}₹{total_net_pnl:,.2f}</div>"
    f"<div class='v48-kpi-sub'>Unreal Gross ₹{unrealized_gross:+,.0f} (Net ₹{unrealized_net:+,.0f}) | Realized ₹{realized_net:+,.2f}</div></div>",
    unsafe_allow_html=True,
)
k4.markdown(
    f"<div class='v48-kpi'><div class='v48-kpi-label'>Composite Trust &amp; Graduation Gate</div>"
    f"<div class='v48-kpi-val'>{trust_data['composite_trust']}/100 (PF {trust_data['profit_factor']:.2f})</div>"
    f"<div class='v48-kpi-sub'>V4.8 Closed {trust_data['v48_closed_count']}/100 | Open: {len(open_trades)}</div></div>",
    unsafe_allow_html=True,
)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
render_filter_watch_table(open_trades=open_trades, key_prefix="overview", on_close_callback=lambda sym: store.close_paper_position(sym))
st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
render_desk_and_agent_scorecards(trust_data)

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
ist_tag = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y%m%d")
ex1, ex2 = st.columns(2)
ex1.download_button("⬇ Download Unified Combined Ledger CSV (All Columns)", data=pd.DataFrame(all_trades).to_csv(index=False).encode("utf-8"), file_name=f"nse_v4_8_combined_ledger_{ist_tag}.csv", mime="text/csv")
ex2.download_button("⬇ Download Complete V4.8 JSON Snapshot", data=json.dumps({"schema": "v4.8", "live_mode": False, "accounts": accounts, "trust": trust_data, "trades": all_trades}, indent=2, default=str).encode("utf-8"), file_name=f"nse_v4_8_snapshot_{ist_tag}.json", mime="application/json")

st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
render_agent_war_room(state["agent_messages"], limit=15, scope_desk="ALL")
