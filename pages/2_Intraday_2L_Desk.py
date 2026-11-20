import streamlit as st
import asyncio
import os
import json
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from swing_engine import get_gsheet_client
from intraday_engine import (
    INTRADAY_SHEET_NAME,
    INIT_EQ_CAPITAL,
    INIT_FNO_CAPITAL,
    EQ_MIS_LEVERAGE,
    ensure_intraday_ledger_initialized,
    fetch_index_fno_context,
    run_unified_cycle,
    black_scholes_price,
)

st.set_page_config(page_title="Desk 2: ₹2L Intraday MIS & Index F&O", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
</style>
""", unsafe_allow_html=True)

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()

@st.cache_data(ttl=25, show_spinner=False)
def load_intraday_sheet_snapshot(_gcp_creds: dict):
    sh_local = get_gsheet_client(_gcp_creds, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(sh_local)
    ws_map = {ws.title: ws for ws in sh_local.worksheets()}
    acct_vals = ws_map["account"].get("A2:E2")
    r2 = acct_vals[0] if acct_vals and acct_vals[0] else []
    eq_c = float(r2[1]) if len(r2) > 1 and r2[1] not in ("", None) else INIT_EQ_CAPITAL
    fno_c = float(r2[2]) if len(r2) > 2 and r2[2] not in ("", None) else INIT_FNO_CAPITAL
    pk_p = float(r2[3]) if len(r2) > 3 and r2[3] not in ("", None) else 0.0
    lk_f = float(r2[4]) if len(r2) > 4 and r2[4] not in ("", None) else 0.0
    t_rec = ws_map["trades"].get_all_records()
    m_rec = ws_map["agent_messages"].get_all_records()
    return eq_c, fno_c, pk_p, lk_f, t_rec, m_rec


try:
    eq_cash, fno_cash, peak_pnl, locked_floor, trades_rec, msgs_rec = load_intraday_sheet_snapshot(gcp_creds)
except Exception as sheet_err:
    st.warning(f"Google Sheets temporarily rate-limited or syncing (`{sheet_err}`). Showing cached/default wallet values — click Refresh Quotes in 10s.")
    eq_cash, fno_cash, peak_pnl, locked_floor, trades_rec, msgs_rec = INIT_EQ_CAPITAL, INIT_FNO_CAPITAL, 0.0, 0.0, [], []
trades_df = pd.DataFrame(trades_rec).iloc[::-1] if trades_rec else pd.DataFrame()
msgs_df = pd.DataFrame(msgs_rec).iloc[::-1].head(100) if msgs_rec else pd.DataFrame()

open_t = trades_df[trades_df["status"] == "OPEN"] if not trades_df.empty else pd.DataFrame()
open_eq = open_t[open_t["book"] == "EQUITY"] if not open_t.empty else pd.DataFrame()
open_fno = open_t[open_t["book"] == "FNO"] if not open_t.empty else pd.DataFrame()

st.markdown("## ⚡ Desk 2: ₹2,00,000 Unified Intraday Equity MIS & Index F&O Terminal")

b1, b2, b3, b4 = st.columns([1.4, 1.3, 1.1, 1.2])
with b1:
    if st.button("⚡ Run Intraday + F&O Cycle", use_container_width=True, type="primary"):
        with st.spinner("Scanning 61 MIS stocks + Option Chains..."):
            asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=False))
        st.cache_data.clear()
        st.rerun()
with b2:
    if st.button("⏰ Force 3:15 PM EOD Square-Off", use_container_width=True):
        asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=True))
        st.cache_data.clear()
        st.warning("All Intraday MIS & F&O positions squared off!")
        st.rerun()
with b3:
    if st.button("🔄 Refresh Quotes", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with b4:
    st.markdown(f"<div style='text-align:right;padding-top:6px;font-size:12.5px;color:#94a3b8;'>Peak: <b>₹{peak_pnl:+,.0f}</b> | Floor: <b>₹{locked_floor:+,.0f}</b></div>", unsafe_allow_html=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Book A: Equity MIS Cash", f"₹{eq_cash:,.2f}", f"5x Buying Power: ₹{eq_cash * EQ_MIS_LEVERAGE:,.0f}")
m2.metric("Book B: Index F&O Cash", f"₹{fno_cash:,.2f}", f"Open F&O Legs: {len(open_fno)} / 2")
m3.metric("Open MIS Positions", f"{len(open_eq)} / 3", "Circuit Breaker: -₹1,500")
m4.metric("Locked Intraday Profit Floor", f"₹{locked_floor:,.2f}", f"Session Peak: ₹{peak_pnl:,.2f}")

t_overview, t_chain, t_payoff, t_ledger = st.tabs([
    "🎯 Active Intraday Desks",
    "📊 NIFTY / BANKNIFTY Option Chain",
    "🧮 Options Payoff Lab",
    "📜 Intraday Ledger & Logs",
])

with t_overview:
    c_eq, c_fno = st.columns(2)
    with c_eq:
        st.markdown("#### 📘 Book A: Active Equity MIS (5x Leverage)")
        if open_eq.empty:
            st.info("No open Equity MIS positions.")
        else:
            st.dataframe(open_eq[["timestamp", "symbol", "strategy", "qty_or_lots", "entry", "sl", "target"]], use_container_width=True, hide_index=True)
    with c_fno:
        st.markdown("#### 📙 Book B: Active Index F&O Options")
        if open_fno.empty:
            st.info("No open NIFTY / BANKNIFTY option contracts.")
        else:
            st.dataframe(open_fno[["timestamp", "symbol", "strategy", "qty_or_lots", "lot_size", "entry", "sl", "target"]], use_container_width=True, hide_index=True)

with t_chain:
    st.markdown("#### 📊 Live 11-Strike Option Chain Matrix (Black-Scholes + India VIX)")
    fno_ctx = fetch_index_fno_context()
    st.caption(f"India VIX: **{fno_ctx.get('india_vix', 14.5)}** • Active Regime: **{fno_ctx.get('vix_regime', 'SINGLE_LEG_BUY_ALLOWED')}**")
    idx_choice = st.selectbox("Select Index", ["NIFTY", "BANKNIFTY"])
    idx_data = fno_ctx.get("indices", {}).get(idx_choice)
    if idx_data:
        ic1, ic2, ic3, ic4 = st.columns(4)
        ic1.metric(f"{idx_choice} Spot", f"{idx_data['spot']:,.2f}", f"{idx_data['intraday_chg_pct']:+.2f}%")
        ic2.metric("ATM Strike", f"{idx_data['atm_strike']}")
        ic3.metric("Lot Size", f"{idx_data['lot_size']} Qty")
        ic4.metric("Synthetic PCR", f"{idx_data['pcr']}")
        st.dataframe(pd.DataFrame(idx_data["chain"]), use_container_width=True, hide_index=True)

with t_payoff:
    st.markdown("#### 🧮 Expiry Payoff Diagram Simulator")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        p_idx = st.selectbox("Index", ["NIFTY", "BANKNIFTY"], key="pay_idx")
    with p2:
        p_strat = st.selectbox("Strategy", ["SINGLE_LEG_BUY", "DEBIT_SPREAD"])
    with p3:
        p_type = st.selectbox("Option Type", ["CE", "PE"])
    with p4:
        p_spot = st.number_input("Current Spot", value=25500.0 if p_idx == "NIFTY" else 63300.0, step=50.0)

    step = 50 if p_idx == "NIFTY" else 100
    lot = 65 if p_idx == "NIFTY" else 30
    k1 = int(round(p_spot / step) * step)
    k2 = k1 + (2 * step if p_type == "CE" else -2 * step)
    prem1 = black_scholes_price(p_spot, k1, 3.0 / 365.0, 0.065, 0.145, p_type)
    prem2 = black_scholes_price(p_spot, k2, 3.0 / 365.0, 0.065, 0.145, p_type) if p_strat == "DEBIT_SPREAD" else 0.0
    net_debit = max(prem1 - prem2, 5.0)

    spots = np.linspace(p_spot * 0.97, p_spot * 1.03, 80)
    payoffs = []
    for s in spots:
        intr1 = max(0.0, s - k1) if p_type == "CE" else max(0.0, k1 - s)
        intr2 = (max(0.0, s - k2) if p_type == "CE" else max(0.0, k2 - s)) if p_strat == "DEBIT_SPREAD" else 0.0
        payoffs.append(((intr1 - intr2) - net_debit) * lot)

    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=spots, y=payoffs, mode="lines", name="Expiry P&L (₹)", line=dict(color="#38bdf8", width=3)))
    fig_p.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    fig_p.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=320, xaxis_title="Expiry Spot", yaxis_title="Net P&L per Lot (₹)")
    st.plotly_chart(fig_p, use_container_width=True)

with t_ledger:
    l1, l2 = st.columns(2)
    with l1:
        st.markdown("#### 📜 Intraday & F&O Ledger (IST)")
        if not trades_df.empty:
            st.dataframe(trades_df, use_container_width=True, hide_index=True)
        else:
            st.write("No intraday trades logged yet.")
    with l2:
        st.markdown("#### 💬 Intraday 5-Agent Stream")
        for _, r in msgs_df.head(25).iterrows():
            with st.chat_message("assistant"):
                st.markdown(f"**`{r['timestamp']}`** — **{r['sender']}**")
                st.write(r["summary"])
