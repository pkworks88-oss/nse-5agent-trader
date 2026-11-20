import streamlit as st
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from swing_engine import get_gsheet_client
from intraday_engine import (
    INTRADAY_SHEET_NAME,
    INIT_EQ_CAPITAL,
    INIT_FNO_CAPITAL,
    EQ_MIS_LEVERAGE,
    EQ_DAILY_LOSS_LIMIT,
    FNO_DAILY_LOSS_LIMIT,
    ensure_intraday_ledger_initialized,
    fetch_index_fno_context,
    parse_fno_symbol,
    days_to_next_tuesday,
    run_unified_cycle,
    black_scholes_price,
)

IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


st.set_page_config(page_title="Desk 2: ₹2L Intraday MIS & Index F&O", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
</style>
""", unsafe_allow_html=True)

current_ist = get_ist_now()
api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()


@st.cache_data(ttl=90, show_spinner=False)
def load_intraday_sheet_snapshot(_gcp_creds: dict):
    sh_local = get_gsheet_client(_gcp_creds, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(sh_local)
    acct_vals = sh_local.worksheet("account").get("A2:E2")
    r2 = acct_vals[0] if acct_vals and acct_vals[0] else []
    eq_c = float(r2[1]) if len(r2) > 1 and r2[1] not in ("", None) else INIT_EQ_CAPITAL
    fno_c = float(r2[2]) if len(r2) > 2 and r2[2] not in ("", None) else INIT_FNO_CAPITAL
    pk_p = float(r2[3]) if len(r2) > 3 and r2[3] not in ("", None) else 0.0
    lk_f = float(r2[4]) if len(r2) > 4 and r2[4] not in ("", None) else 0.0
    t_rec = sh_local.worksheet("trades").get_all_records()
    m_rec = sh_local.worksheet("agent_messages").get_all_records()
    return eq_c, fno_c, pk_p, lk_f, t_rec, m_rec


@st.cache_data(ttl=90, show_spinner=False)
def cached_fno_context():
    return fetch_index_fno_context()


@st.cache_data(ttl=60, show_spinner=False)
def get_intraday_candles(ticker: str, period: str = "2d", interval: str = "5m") -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        return df.dropna()
    except Exception:
        return pd.DataFrame()


try:
    eq_cash, fno_cash, peak_pnl, locked_floor, trades_rec, msgs_rec = load_intraday_sheet_snapshot(gcp_creds)
except Exception as sheet_err:
    st.warning(f"Google Sheets temporarily rate-limited or syncing (`{sheet_err}`). Showing cached/default wallet values — click Refresh Quotes in 15s.")
    eq_cash, fno_cash, peak_pnl, locked_floor, trades_rec, msgs_rec = INIT_EQ_CAPITAL, INIT_FNO_CAPITAL, 0.0, 0.0, [], []

trades_df = pd.DataFrame(trades_rec).iloc[::-1] if trades_rec else pd.DataFrame()
msgs_df = pd.DataFrame(msgs_rec).iloc[::-1].head(100) if msgs_rec else pd.DataFrame()

open_t = trades_df[trades_df["status"] == "OPEN"].copy() if not trades_df.empty else pd.DataFrame()
open_eq = open_t[open_t["book"] == "EQUITY"].copy() if not open_t.empty else pd.DataFrame()
open_fno = open_t[open_t["book"] == "FNO"].copy() if not open_t.empty else pd.DataFrame()

fno_ctx = cached_fno_context()
india_vix = float(fno_ctx.get("india_vix", 14.5))
sigma = max(0.11, min(0.35, india_vix / 100.0))
nifty_spot_live = float(fno_ctx.get("indices", {}).get("NIFTY", {}).get("spot", 25500.0))
bnifty_spot_live = float(fno_ctx.get("indices", {}).get("BANKNIFTY", {}).get("spot", 63300.0))

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

mins_now = current_ist.hour * 60 + current_ist.minute
mis_cutoff_txt = "OPEN (< 2:00 PM)" if mins_now < 14 * 60 else "CLOSED (≥ 2:00 PM)"
eod_cutoff_txt = "ACTIVE (< 3:15 PM)" if mins_now < 15 * 60 + 15 else "SQUARE-OFF WINDOW"
st.caption(f"IST Clock: **{current_ist.strftime('%I:%M:%S %p')}** • MIS Entry Cutoff: **{mis_cutoff_txt}** • Forced EOD Square-Off: **3:15 PM IST ({eod_cutoff_txt})** • Circuit Breakers: **MIS ₹{EQ_DAILY_LOSS_LIMIT:,.0f} / F&O ₹{FNO_DAILY_LOSS_LIMIT:,.0f}**")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Book A: Equity MIS Cash", f"₹{eq_cash:,.2f}", f"5x Buying Power: ₹{eq_cash * EQ_MIS_LEVERAGE:,.0f}")
m2.metric("Book B: Index F&O Cash", f"₹{fno_cash:,.2f}", f"Open F&O Legs: {len(open_fno)} / 2")
m3.metric("Open MIS Positions", f"{len(open_eq)} / 3", f"VIX: {india_vix:.2f}")
m4.metric("Locked Intraday Profit Floor", f"₹{locked_floor:,.2f}", f"Session Peak: ₹{peak_pnl:,.2f}")

t_overview, t_chart, t_chain, t_payoff, t_ledger = st.tabs([
    "🎯 Active Intraday Desks",
    "📈 Live Intraday & F&O Chart",
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
            eq_disp = []
            for _, r in open_eq.iterrows():
                sym = str(r["symbol"])
                qty = int(float(r["qty_or_lots"]))
                entry = float(r["entry"])
                df_q = get_intraday_candles(sym, "2d", "5m")
                ltp = float(df_q["Close"].iloc[-1]) if not df_q.empty else entry
                mtm = round((ltp - entry) * qty, 2)
                eq_disp.append({
                    "Symbol": sym, "Strategy": r["strategy"], "Qty": qty,
                    "Entry (₹)": round(entry, 2), "LTP (₹)": round(ltp, 2),
                    "SL (₹)": round(float(r["sl"]), 2), "Target (₹)": round(float(r["target"]), 2),
                    "MTM P&L (₹)": mtm,
                })
            st.dataframe(pd.DataFrame(eq_disp), use_container_width=True, hide_index=True)

    with c_fno:
        st.markdown("#### 📙 Book B: Active Index F&O Options")
        if open_fno.empty:
            st.info("No open NIFTY / BANKNIFTY option contracts.")
        else:
            fno_disp = []
            for _, r in open_fno.iterrows():
                sym = str(r["symbol"])
                lots = int(float(r["qty_or_lots"]))
                lot_sz = int(float(r.get("lot_size", 1) or 1))
                entry = float(r["entry"])
                parsed = parse_fno_symbol(sym)
                prem_now = entry
                if parsed:
                    sp_now = nifty_spot_live if parsed["index_name"] == "NIFTY" else bnifty_spot_live
                    t_yr = (days_to_next_tuesday() if parsed["index_name"] == "NIFTY" else 14.0) / 365.0
                    p1 = black_scholes_price(sp_now, parsed["strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                    if parsed["hedge_strike"] > 0:
                        p2 = black_scholes_price(sp_now, parsed["hedge_strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                        prem_now = round(max(p1 - p2, 1.0), 2)
                    else:
                        prem_now = p1
                mtm = round((prem_now - entry) * lots * lot_sz, 2)
                fno_disp.append({
                    "Contract": sym, "Strategy": r["strategy"], "Lots": lots, "Lot Size": lot_sz,
                    "Entry (₹)": round(entry, 2), "Prem Now (₹)": round(prem_now, 2),
                    "SL (₹)": round(float(r["sl"]), 2), "Target (₹)": round(float(r["target"]), 2),
                    "MTM P&L (₹)": mtm,
                })
            st.dataframe(pd.DataFrame(fno_disp), use_container_width=True, hide_index=True)

with t_chart:
    st.markdown("#### 📈 Live Intraday Candlestick + VWAP + EMA 9/20 + Bracket Levels")
    open_mis_syms = open_eq["symbol"].astype(str).unique().tolist() if not open_eq.empty else []
    chart_options = list(dict.fromkeys(
        open_mis_syms + ["NIFTY 50 (^NSEI)", "BANK NIFTY (^NSEBANK)", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"]
    ))
    ic1, ic2, ic3 = st.columns([2.0, 1.2, 1.2])
    with ic1:
        pick_lbl = st.selectbox("Select MIS Stock or Index Spot", chart_options, key="intra_chart_pick")
        if "NIFTY 50" in pick_lbl:
            yf_sym = "^NSEI"
        elif "BANK NIFTY" in pick_lbl:
            yf_sym = "^NSEBANK"
        else:
            yf_sym = pick_lbl
    with ic2:
        intra_tf = st.selectbox("Intraday Interval", ["5m (2 Days)", "15m (5 Days)"], key="intra_tf")
    with ic3:
        show_levels = st.checkbox("Show VWAP, EMA 9/20 & Brackets", value=True, key="intra_ov")

    p_str, i_str = ("2d", "5m") if "5m" in intra_tf else ("5d", "15m")
    c_df = get_intraday_candles(yf_sym, p_str, i_str)
    if c_df.empty:
        st.warning(f"No intraday candle data available for {yf_sym}.")
    else:
        typ_px = (c_df["High"] + c_df["Low"] + c_df["Close"]) / 3.0
        c_df["VWAP"] = (typ_px * c_df["Volume"]).cumsum() / c_df["Volume"].cumsum().replace(0, 1)
        c_df["EMA9"] = c_df["Close"].ewm(span=9).mean()
        c_df["EMA20"] = c_df["Close"].ewm(span=20).mean()

        fig_ic = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.76, 0.24])
        fig_ic.add_trace(go.Candlestick(
            x=c_df.index, open=c_df["Open"], high=c_df["High"], low=c_df["Low"], close=c_df["Close"],
            name=yf_sym, increasing_line_color="#10b981", decreasing_line_color="#ef4444"
        ), row=1, col=1)

        y_lo, y_hi = float(c_df["Low"].min()), float(c_df["High"].max())
        if show_levels:
            fig_ic.add_trace(go.Scatter(x=c_df.index, y=c_df["VWAP"], mode="lines", name="VWAP", line=dict(color="#06b6d4", width=1.6, dash="dot")), row=1, col=1)
            fig_ic.add_trace(go.Scatter(x=c_df.index, y=c_df["EMA9"], mode="lines", name="EMA 9", line=dict(color="#f59e0b", width=1.2)), row=1, col=1)
            fig_ic.add_trace(go.Scatter(x=c_df.index, y=c_df["EMA20"], mode="lines", name="EMA 20", line=dict(color="#a855f7", width=1.2)), row=1, col=1)

            if not open_eq.empty and yf_sym in open_eq["symbol"].values:
                m_row = open_eq[open_eq["symbol"] == yf_sym].iloc[0]
                e_p, s_p, t_p = float(m_row["entry"]), float(m_row["sl"]), float(m_row["target"])
                y_lo, y_hi = min(y_lo, s_p), max(y_hi, t_p)
                fig_ic.add_hline(y=e_p, line_dash="dash", line_color="#38bdf8", annotation_text=f"Entry ₹{e_p:,.2f}", row=1, col=1)
                fig_ic.add_hline(y=s_p, line_dash="dot", line_color="#f87171", annotation_text=f"SL ₹{s_p:,.2f}", row=1, col=1)
                fig_ic.add_hline(y=t_p, line_dash="dash", line_color="#34d399", annotation_text=f"Target ₹{t_p:,.2f}", row=1, col=1)

        v_cols = ["#10b981" if c >= o else "#ef4444" for c, o in zip(c_df["Close"], c_df["Open"])]
        fig_ic.add_trace(go.Bar(x=c_df.index, y=c_df["Volume"], marker_color=v_cols, name="Volume", showlegend=False), row=2, col=1)
        fig_ic.update_yaxes(range=[y_lo * 0.997, y_hi * 1.003], title_text="Price (₹)", row=1, col=1)
        fig_ic.update_yaxes(title_text="Volume", row=2, col=1)
        fig_ic.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[15.5, 9.25], pattern="hour")])
        fig_ic.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=480, margin=dict(l=8, r=8, t=20, b=8), xaxis_rangeslider_visible=False)
        st.plotly_chart(fig_ic, use_container_width=True)

with t_chain:
    st.markdown("#### 📊 Live 11-Strike Option Chain Matrix (Black-Scholes + India VIX)")
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
    st.markdown("#### 🧮 Expiry Payoff Diagram Simulator (Synced to Live Spot)")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        p_idx = st.selectbox("Index", ["NIFTY", "BANKNIFTY"], key="pay_idx")
    with p2:
        p_strat = st.selectbox("Strategy", ["SINGLE_LEG_BUY", "DEBIT_SPREAD"])
    with p3:
        p_type = st.selectbox("Option Type", ["CE", "PE"])
    default_spot = nifty_spot_live if p_idx == "NIFTY" else bnifty_spot_live
    with p4:
        p_spot = st.number_input("Current Spot (Auto-Synced)", value=float(default_spot), step=50.0)

    step = 50 if p_idx == "NIFTY" else 100
    lot = 65 if p_idx == "NIFTY" else 30
    k1 = int(round(p_spot / step) * step)
    k2 = k1 + (2 * step if p_type == "CE" else -2 * step)
    prem1 = black_scholes_price(p_spot, k1, 3.0 / 365.0, 0.065, sigma, p_type)
    prem2 = black_scholes_price(p_spot, k2, 3.0 / 365.0, 0.065, sigma, p_type) if p_strat == "DEBIT_SPREAD" else 0.0
    net_debit = max(prem1 - prem2, 5.0)
    max_loss = round(net_debit * lot, 2)
    max_gain_str = f"₹{((2 * step) - net_debit) * lot:,.0f}" if p_strat == "DEBIT_SPREAD" else "Uncapped / High"
    be_spot = round(k1 + net_debit if p_type == "CE" else k1 - net_debit, 2)

    pm1, pm2, pm3 = st.columns(3)
    pm1.metric("Max Risk / Lot", f"-₹{max_loss:,.2f}")
    pm2.metric("Max Payoff / Lot", max_gain_str)
    pm3.metric("Breakeven Spot", f"{be_spot:,.2f}")

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
