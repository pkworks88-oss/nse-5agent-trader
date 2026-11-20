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
from skills.market_skills import compute_adx_series, get_sector
from intraday_backtest import run_intraday_mis_backtest
import sys
from pathlib import Path
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
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
    close_specific_intraday_deals,
    compute_intraday_agent_trust,
    compute_book_and_combined_trust,
    get_recent_intraday_lessons,
    CAS_DEC7_2026_ENABLED,
)

IST = timezone(timedelta(hours=5, minutes=30))




def format_tier_pill(tier_str: str) -> str:
    t_up = str(tier_str).upper()
    if "LOW" in t_up:
        bg, fg, br = "rgba(16, 185, 129, 0.16)", "#34d399", "rgba(16, 185, 129, 0.35)"
    elif "HIGH" in t_up:
        bg, fg, br = "rgba(239, 68, 68, 0.18)", "#f87171", "rgba(239, 68, 68, 0.38)"
    else:
        bg, fg, br = "rgba(245, 158, 11, 0.16)", "#fbbf24", "rgba(245, 158, 11, 0.35)"
    return (
        f"<span style='background:{bg};color:{fg};border:1px solid {br};"
        f"padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:0.4px;'>{t_up}</span>"
    )


def format_protection_pill(entry: float, sl: float, units: int) -> str:
    if sl > entry:
        locked_amt = round((sl - entry) * units, 0)
        return (
            f"<span style='background:rgba(16, 185, 129, 0.18);color:#34d399;border:1px solid rgba(16, 185, 129, 0.4);"
            f"padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>Locked +₹{locked_amt:,.0f}</span>"
        )
    return (
        f"<span style='background:rgba(239, 68, 68, 0.18);color:#f87171;border:1px solid rgba(239, 68, 68, 0.4);"
        f"padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>At risk</span>"
    )


def render_colorful_positions_table(rows_data: list[dict], title: str = "Unified live positions & F&O watch table"):
    if not rows_data:
        return
    html_parts = [
        f"<div class='panel-box' style='padding:14px;margin-top:6px;margin-bottom:10px;'>",
        f"<div style='font-size:16px;font-weight:700;color:#f1f5f9;margin-bottom:12px;'>{title}</div>",
        f"<div style='overflow-x:auto;'>",
        f"<table style='width:100%;border-collapse:collapse;font-size:13px;'>",
        f"<thead><tr style='border-bottom:1px solid #1e293b;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.6px;'>",
        f"<th style='text-align:left;padding:8px 10px;'>Symbol</th>",
        f"<th style='text-align:left;padding:8px 10px;'>Desk</th>",
        f"<th style='text-align:left;padding:8px 10px;'>Tier</th>",
        f"<th style='text-align:right;padding:8px 10px;'>Qty</th>",
        f"<th style='text-align:right;padding:8px 10px;'>Entry ₹</th>",
        f"<th style='text-align:right;padding:8px 10px;'>LTP / Now ₹</th>",
        f"<th style='text-align:right;padding:8px 10px;'>Stop ₹</th>",
        f"<th style='text-align:right;padding:8px 10px;'>Target ₹</th>",
        f"<th style='text-align:center;padding:8px 10px;'>Protection</th>",
        f"<th style='text-align:right;padding:8px 10px;'>MTM P&L ₹</th>",
        f"</tr></thead><tbody>",
    ]
    for rd in rows_data:
        mtm_val = float(rd.get("mtm", 0.0))
        mtm_col = "#34d399" if mtm_val >= 0 else "#f87171"
        mtm_fmt = f"+{mtm_val:,.2f}" if mtm_val > 0 else f"{mtm_val:,.2f}"
        tier_badge = format_tier_pill(rd.get("tier", "MID"))
        prot_badge = format_protection_pill(float(rd["entry"]), float(rd["sl"]), int(rd["units"]))
        html_parts.append(
            f"<tr style='border-bottom:1px solid rgba(30,41,59,0.65);'>"
            f"<td style='padding:10px;font-weight:700;color:#f8fafc;'>{rd['symbol']}</td>"
            f"<td style='padding:10px;color:#cbd5e1;'>{rd['desk']}</td>"
            f"<td style='padding:10px;'>{tier_badge}</td>"
            f"<td style='padding:10px;text-align:right;color:#e2e8f0;'>{int(rd['qty']):,}</td>"
            f"<td style='padding:10px;text-align:right;color:#cbd5e1;'>{float(rd['entry']):,.2f}</td>"
            f"<td style='padding:10px;text-align:right;color:#f8fafc;font-weight:600;'>{float(rd['ltp']):,.2f}</td>"
            f"<td style='padding:10px;text-align:right;color:#cbd5e1;'>{float(rd['sl']):,.2f}</td>"
            f"<td style='padding:10px;text-align:right;color:#cbd5e1;'>{float(rd['target']):,.2f}</td>"
            f"<td style='padding:10px;text-align:center;'>{prot_badge}</td>"
            f"<td style='padding:10px;text-align:right;font-weight:700;color:{mtm_col};'>{mtm_fmt}</td>"
            f"</tr>"
        )
    html_parts.append("</tbody></table></div></div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def describe_trail_tier(entry: float, sl: float, target: float, current_px: float, book: str = "EQUITY") -> tuple[str, str]:
    dist = max(target - entry, 0.5)
    prog_pct = round(((current_px - entry) / dist) * 100.0, 1)
    be_thresh = entry * 1.001 if book == "EQUITY" else entry + 1.5
    if sl >= entry + 0.60 * dist:
        tier = "80% Lock (65%)"
    elif sl >= entry + 0.35 * dist:
        tier = "60% Lock (40%)"
    elif sl >= entry + 0.15 * dist:
        tier = "40% Lock (20%)"
    elif sl >= be_thresh - 0.05:
        tier = "20% BE Buffer"
    else:
        tier = "Initial SL"
    return f"{prog_pct:+.1f}%", tier


def render_risk_floor_gauge(today_combined_pnl: float, peak_val: float, floor_val: float):
    combined_loss_limit = EQ_DAILY_LOSS_LIMIT + FNO_DAILY_LOSS_LIMIT
    st.markdown("##### 🎯 Intraday Circuit-Breaker & Stepped Profit-Floor Gauge")
    g_cols = st.columns([1.2, 2.6, 1.2])
    with g_cols[0]:
        st.caption(f"Loss Cap: **₹{combined_loss_limit:,.0f}**")
    with g_cols[1]:
        span = 5000.0 - combined_loss_limit
        norm_pos = max(0.0, min(1.0, (today_combined_pnl - combined_loss_limit) / span))
        st.progress(norm_pos, text=f"Today Combined P&L: ₹{today_combined_pnl:+,.2f} | Peak: ₹{peak_val:+,.0f} | Floor: ₹{floor_val:+,.0f}")
    with g_cols[2]:
        st.caption("Rungs: **₹3k→₹1.5k** | **₹4k→₹3k** | **₹5k+→75%**")

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
    raw_t = sh_local.worksheet("trades").get_all_records()
    for i_r, r_item in enumerate(raw_t, start=2):
        r_item["_sheet_row"] = i_r
    t_rec = raw_t
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
cas_mode_ui = st.sidebar.toggle("📅 NSE Dec 7, 2026 CAS Schedule", value=CAS_DEC7_2026_ENABLED, key="cas_dec7_toggle")
sq_cutoff_min = (15 * 60 + 25) if cas_mode_ui else (15 * 60 + 15)
sq_lbl = "3:25 PM (Dec 7 CAS)" if cas_mode_ui else "3:15 PM IST"

if mins_now < 9 * 60 + 15:
    sess_badge = "🌙 PRE-MARKET"
elif mins_now < 14 * 60:
    sess_badge = "🟢 LIVE ENTRY WINDOW (< 2:00 PM)"
elif mins_now < sq_cutoff_min:
    sess_badge = f"🟡 NO NEW ENTRIES / TRAILING & FLOOR ACTIVE (≥ 2:00 PM → {sq_lbl})"
elif mins_now <= (15 * 60 + 45 if cas_mode_ui else 15 * 60 + 30):
    sess_badge = f"🔴 EOD SQUARE-OFF / CAS WINDOW ({sq_lbl})"
else:
    sess_badge = "🌙 MARKET CLOSED"

trust_scores = compute_intraday_agent_trust(trades_rec)
lessons_list = get_recent_intraday_lessons(msgs_rec, limit=5)

next_cron_min = ((mins_now // 5) + 1) * 5
next_h, next_m = divmod(next_cron_min % (24 * 60), 60)
next_hb_str = f"{next_h:02d}:{next_m:02d} IST"
vix_regime_lbl = f"VIX {india_vix:.2f} ({fno_ctx.get('vix_regime', 'SINGLE_LEG_BUY_ALLOWED')})"

top_b1, top_b2 = st.columns([3.8, 1.2])
with top_b1:
    st.markdown(
        f"<div class='panel-box' style='margin-bottom:10px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;'>"
        f"<div><b>Session:</b> {sess_badge} &nbsp;|&nbsp; <b>IST:</b> {current_ist.strftime('%I:%M:%S %p')} &nbsp;|&nbsp; <b>Next 5m Cycle:</b> {next_hb_str}</div>"
        f"<div><b>{vix_regime_lbl}</b> &nbsp;|&nbsp; <b>Trust:</b> AG1 <b>{trust_scores['AG1_LOW_RISK']}</b> • AG2 <b>{trust_scores['AG2_MID_RISK']}</b> • AG3 <b>{trust_scores['AG3_HIGH_RISK']}</b></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
with top_b2:
    mobile_card_mode = st.toggle("📱 Compact Card View", value=False, key="toggle_mobile_cards")

today_str_ui = current_ist.strftime("%Y-%m-%d")
today_closed_ui = trades_df[(trades_df["status"] != "OPEN") & (trades_df["date"].astype(str) == today_str_ui)] if not trades_df.empty else pd.DataFrame()
today_realized_pnl = float(pd.to_numeric(today_closed_ui["net_pnl"], errors="coerce").fillna(0.0).sum()) if not today_closed_ui.empty else 0.0

eq_locked_margin = 0.0
eq_unreal_mtm = 0.0
eq_row_mtm = {}
for _, r_eq in open_eq.iterrows():
    s_eq = str(r_eq["symbol"])
    q_eq = int(float(r_eq["qty_or_lots"]))
    e_eq = float(r_eq["entry"])
    df_q_tmp = get_intraday_candles(s_eq, "2d", "5m")
    l_eq = float(df_q_tmp["Close"].iloc[-1]) if not df_q_tmp.empty else e_eq
    mtm_v = round((l_eq - e_eq) * q_eq, 2)
    eq_row_mtm[int(r_eq.name)] = (l_eq, mtm_v)
    eq_locked_margin += (e_eq * q_eq) / EQ_MIS_LEVERAGE
    eq_unreal_mtm += mtm_v

fno_locked_prem = 0.0
fno_unreal_mtm = 0.0
fno_row_mtm = {}
for _, r_fn in open_fno.iterrows():
    s_fn = str(r_fn["symbol"])
    l_fn = int(float(r_fn["qty_or_lots"]))
    ls_fn = int(float(r_fn.get("lot_size", 1) or 1))
    e_fn = float(r_fn["entry"])
    p_fn = parse_fno_symbol(s_fn)
    pr_now = e_fn
    if p_fn:
        sp_n = nifty_spot_live if p_fn["index_name"] == "NIFTY" else bnifty_spot_live
        t_y = (days_to_next_tuesday() if p_fn["index_name"] == "NIFTY" else 14.0) / 365.0
        p1_v = black_scholes_price(sp_n, p_fn["strike"], t_y, 0.065, sigma, p_fn["opt_type"])
        if p_fn["hedge_strike"] > 0:
            p2_v = black_scholes_price(sp_n, p_fn["hedge_strike"], t_y, 0.065, sigma, p_fn["opt_type"])
            pr_now = round(max(p1_v - p2_v, 1.0), 2)
        else:
            pr_now = p1_v
    mtm_f = round((pr_now - e_fn) * l_fn * ls_fn, 2)
    fno_row_mtm[int(r_fn.name)] = (pr_now, mtm_f)
    fno_locked_prem += e_fn * l_fn * ls_fn
    fno_unreal_mtm += mtm_f

today_combined_pnl = round(today_realized_pnl + eq_unreal_mtm + fno_unreal_mtm, 2)
render_risk_floor_gauge(today_combined_pnl, peak_pnl, locked_floor)
st.caption(
    f"Capital Split — **Book A (MIS):** Free Cash ₹{eq_cash:,.2f} | Locked Margin ₹{eq_locked_margin:,.2f} | Unrealized MTM ₹{eq_unreal_mtm:+,.2f} &nbsp;||&nbsp; "
    f"**Book B (F&O):** Free Cash ₹{fno_cash:,.2f} | Locked Premium ₹{fno_locked_prem:,.2f} | Unrealized MTM ₹{fno_unreal_mtm:+,.2f}"
)

book_trust = compute_book_and_combined_trust(trades_rec, None)
open_downside_risk = 0.0
for _, r_eq2 in open_eq.iterrows():
    e_v = float(r_eq2["entry"])
    s_v = float(r_eq2["sl"])
    q_v = int(float(r_eq2["qty_or_lots"]))
    if s_v < e_v:
        open_downside_risk += (e_v - s_v) * q_v
for _, r_fn2 in open_fno.iterrows():
    e_v = float(r_fn2["entry"])
    s_v = float(r_fn2["sl"])
    q_v = int(float(r_fn2["qty_or_lots"])) * int(float(r_fn2.get("lot_size", 1) or 1))
    if s_v < e_v:
        open_downside_risk += (e_v - s_v) * q_v

with st.expander("🧭 Agent Trust by Desk (Combined vs. MIS vs. F&O) & Open Risk to Stops", expanded=False):
    st.write(f"**Net Downside Risk to Active Stops (un-trailed legs):** `₹{open_downside_risk:,.2f}`")
    t_rows = []
    for ag_n in ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]:
        t_rows.append({
            "Agent": ag_n,
            "Combined": book_trust["COMBINED"][ag_n],
            "Intraday MIS": book_trust["EQUITY_MIS"][ag_n],
            "Index F&O": book_trust["FNO"][ag_n],
            "Swing (Base/Closed)": book_trust["SWING"][ag_n],
        })
    st.dataframe(pd.DataFrame(t_rows), use_container_width=True, hide_index=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Book A: Equity MIS Cash", f"₹{eq_cash:,.2f}", f"5x Buying Power: ₹{eq_cash * EQ_MIS_LEVERAGE:,.0f}")
m2.metric("Book B: Index F&O Cash", f"₹{fno_cash:,.2f}", f"Open F&O Legs: {len(open_fno)} / 2")
m3.metric("Open MIS Positions", f"{len(open_eq)} / 3", f"VIX: {india_vix:.2f}")
m4.metric("Locked Intraday Profit Floor", f"₹{locked_floor:,.2f}", f"Session Peak: ₹{peak_pnl:,.2f}")

t_overview, t_chart, t_chain, t_payoff, t_analytics, t_ledger = st.tabs([
    "🎯 Active Intraday Desks",
    "📈 Live Intraday & F&O Chart",
    "📊 NIFTY / BANKNIFTY Option Chain",
    "🧮 Options Payoff Lab",
    "📉 P&L Graphs & 5m Replay",
    "📜 Intraday Ledger & Lessons",
])

with t_overview:
    c_eq, c_fno = st.columns(2)
    with c_eq:
        eq_h1, eq_h2 = st.columns([2.2, 1.0])
        with eq_h1:
            st.markdown("#### 📘 Book A: Active Equity MIS (5x Leverage)")
        with eq_h2:
            if not open_eq.empty and "_sheet_row" in open_eq.columns:
                if st.button("🛑 Flatten MIS", key="btn_flat_eq", use_container_width=True):
                    ok_fe, msg_fe = close_specific_intraday_deals(gcp_creds, [int(x) for x in open_eq["_sheet_row"].dropna()], reason="MANUAL_FLATTEN_MIS")
                    st.cache_data.clear()
                    st.info(msg_fe)
                    st.rerun()
        if open_eq.empty:
            st.info("No open Equity MIS positions.")
        else:
            eq_disp = []
            for idx_r, r in open_eq.iterrows():
                sym = str(r["symbol"])
                qty = int(float(r["qty_or_lots"]))
                entry = float(r["entry"])
                sl_v = float(r["sl"])
                tp_v = float(r["target"])
                ltp, mtm = eq_row_mtm.get(int(idx_r), (entry, 0.0))
                prog_str, tier_str = describe_trail_tier(entry, sl_v, tp_v, ltp, book="EQUITY")
                eq_disp.append({
                    "Symbol": sym, "Qty": qty,
                    "Entry (₹)": round(entry, 2), "LTP (₹)": round(ltp, 2),
                    "SL (₹)": round(sl_v, 2), "Target (₹)": round(tp_v, 2),
                    "Prog %": prog_str, "Trail Tier": tier_str,
                    "MTM (₹)": mtm,
                })
            if mobile_card_mode:
                for item in eq_disp:
                    st.markdown(
                        f"<div class='panel-box' style='margin-bottom:8px;padding:10px;'>"
                        f"<b>{item['Symbol']}</b> ({item['Qty']} qty) • MTM: <b>₹{item['MTM (₹)']:+,.2f}</b><br/>"
                        f"Entry ₹{item['Entry (₹)']} → LTP ₹{item['LTP (₹)']} | SL ₹{item['SL (₹)']} | TP ₹{item['Target (₹)']}<br/>"
                        f"Progress: <b>{item['Prog %']}</b> • Tier: <b>{item['Trail Tier']}</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            else:
                eq_color_rows = []
                for idx_r, r in open_eq.iterrows():
                    sym_c = str(r["symbol"])
                    qty_c = int(float(r["qty_or_lots"]))
                    ent_c = float(r["entry"])
                    sl_c = float(r["sl"])
                    tp_c = float(r["target"])
                    ltp_c, mtm_c = eq_row_mtm.get(int(idx_r), (ent_c, 0.0))
                    t_lbl = str(r.get("strategy", "MIS_MID")).replace("MIS_", "")
                    eq_color_rows.append({
                        "symbol": sym_c, "desk": "Intraday MIS", "tier": t_lbl,
                        "qty": qty_c, "units": qty_c, "entry": ent_c, "ltp": ltp_c,
                        "sl": sl_c, "target": tp_c, "mtm": mtm_c,
                    })
                render_colorful_positions_table(eq_color_rows, title="Book A — Live Equity MIS Positions & Protection")
            eq_choices = {
                f"Row {int(r['_sheet_row'])}: {r['symbol']} ({int(float(r['qty_or_lots']))} qty @ ₹{float(r['entry']):.2f})": int(r["_sheet_row"])
                for _, r in open_eq.iterrows()
                if "_sheet_row" in r and pd.notna(r["_sheet_row"])
            }
            if eq_choices:
                mc1, mc2 = st.columns([2.2, 1.0])
                with mc1:
                    sel_eq = st.selectbox("Select MIS Deal to Close", list(eq_choices.keys()), key="sel_close_eq")
                with mc2:
                    st.markdown("<div style='padding-top:26px;'></div>", unsafe_allow_html=True)
                    if st.button("❌ Close MIS Deal", key="btn_close_eq", use_container_width=True):
                        ok_c, msg_c = close_specific_intraday_deals(gcp_creds, [eq_choices[sel_eq]], reason="MANUAL_CLOSE")
                        st.cache_data.clear()
                        if ok_c:
                            st.success(msg_c)
                            st.rerun()
                        else:
                            st.warning(msg_c)

    with c_fno:
        fn_h1, fn_h2 = st.columns([2.2, 1.0])
        with fn_h1:
            st.markdown("#### 📙 Book B: Active Index F&O Options")
        with fn_h2:
            if not open_fno.empty and "_sheet_row" in open_fno.columns:
                if st.button("🛑 Flatten F&O", key="btn_flat_fno", use_container_width=True):
                    ok_ff, msg_ff = close_specific_intraday_deals(gcp_creds, [int(x) for x in open_fno["_sheet_row"].dropna()], reason="MANUAL_FLATTEN_FNO")
                    st.cache_data.clear()
                    st.info(msg_ff)
                    st.rerun()
        if open_fno.empty:
            st.info("No open NIFTY / BANKNIFTY option contracts.")
        else:
            fno_disp = []
            for idx_r, r in open_fno.iterrows():
                sym = str(r["symbol"])
                lots = int(float(r["qty_or_lots"]))
                entry = float(r["entry"])
                sl_v = float(r["sl"])
                tp_v = float(r["target"])
                prem_now, mtm = fno_row_mtm.get(int(idx_r), (entry, 0.0))
                prog_str, tier_str = describe_trail_tier(entry, sl_v, tp_v, prem_now, book="FNO")
                fno_disp.append({
                    "Contract": sym, "Lots": lots,
                    "Entry (₹)": round(entry, 2), "Now (₹)": round(prem_now, 2),
                    "SL (₹)": round(sl_v, 2), "Target (₹)": round(tp_v, 2),
                    "Prog %": prog_str, "Trail Tier": tier_str,
                    "MTM (₹)": mtm,
                })
            if mobile_card_mode:
                for item in fno_disp:
                    st.markdown(
                        f"<div class='panel-box' style='margin-bottom:8px;padding:10px;'>"
                        f"<b>{item['Contract']}</b> ({item['Lots']} lot) • MTM: <b>₹{item['MTM (₹)']:+,.2f}</b><br/>"
                        f"Entry ₹{item['Entry (₹)']} → Now ₹{item['Now (₹)']} | SL ₹{item['SL (₹)']} | TP ₹{item['Target (₹)']}<br/>"
                        f"Progress: <b>{item['Prog %']}</b> • Tier: <b>{item['Trail Tier']}</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            else:
                fno_color_rows = []
                for idx_r, r in open_fno.iterrows():
                    sym_c = str(r["symbol"])
                    lots_c = int(float(r["qty_or_lots"]))
                    ls_c = int(float(r.get("lot_size", 1) or 1))
                    ent_c = float(r["entry"])
                    sl_c = float(r["sl"])
                    tp_c = float(r["target"])
                    now_c, mtm_c = fno_row_mtm.get(int(idx_r), (ent_c, 0.0))
                    fno_color_rows.append({
                        "symbol": sym_c, "desk": "Index F&O", "tier": "MID",
                        "qty": lots_c, "units": lots_c * ls_c, "entry": ent_c, "ltp": now_c,
                        "sl": sl_c, "target": tp_c, "mtm": mtm_c,
                    })
                render_colorful_positions_table(fno_color_rows, title="Book B — Live Index F&O Positions & Protection")
            fno_choices = {
                f"Row {int(r['_sheet_row'])}: {r['symbol']} ({int(float(r['qty_or_lots']))} lot @ ₹{float(r['entry']):.2f})": int(r["_sheet_row"])
                for _, r in open_fno.iterrows()
                if "_sheet_row" in r and pd.notna(r["_sheet_row"])
            }
            if fno_choices:
                fc1, fc2 = st.columns([2.2, 1.0])
                with fc1:
                    sel_fno = st.selectbox("Select F&O Deal to Close", list(fno_choices.keys()), key="sel_close_fno")
                with fc2:
                    st.markdown("<div style='padding-top:26px;'></div>", unsafe_allow_html=True)
                    if st.button("❌ Close F&O Deal", key="btn_close_fno", use_container_width=True):
                        ok_f, msg_f = close_specific_intraday_deals(gcp_creds, [fno_choices[sel_fno]], reason="MANUAL_CLOSE")
                        st.cache_data.clear()
                        if ok_f:
                            st.success(msg_f)
                            st.rerun()
                        else:
                            st.warning(msg_f)

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

        fig_ic = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.58, 0.20, 0.22])
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

        c_df["ADX14"] = compute_adx_series(c_df["High"], c_df["Low"], c_df["Close"], period=14)
        v_cols = ["#10b981" if c >= o else "#ef4444" for c, o in zip(c_df["Close"], c_df["Open"])]
        fig_ic.add_trace(go.Bar(x=c_df.index, y=c_df["Volume"], marker_color=v_cols, name="Volume", showlegend=False), row=2, col=1)
        fig_ic.add_trace(go.Scatter(x=c_df.index, y=c_df["ADX14"], mode="lines", name="ADX(14)", line=dict(color="#eab308", width=1.6)), row=3, col=1)
        fig_ic.add_hline(y=22.0, line_dash="dot", line_color="#38bdf8", row=3, col=1)
        fig_ic.update_yaxes(range=[y_lo * 0.997, y_hi * 1.003], title_text="Price (₹)", row=1, col=1)
        fig_ic.update_yaxes(title_text="Volume", row=2, col=1)
        fig_ic.update_yaxes(title_text="ADX(14)", row=3, col=1)
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


with t_analytics:
    st.markdown("#### 📉 Daily P&L Bar Chart, Cumulative Curve & 5m Replay Backtester")
    if not trades_df.empty:
        closed_only = trades_df[trades_df["status"] != "OPEN"].copy()
        if not closed_only.empty:
            closed_only["net_pnl_num"] = pd.to_numeric(closed_only["net_pnl"], errors="coerce").fillna(0.0)
            closed_only["date_str"] = closed_only["date"].astype(str)
            daily_grp = closed_only.groupby("date_str", sort=True)["net_pnl_num"].sum().reset_index()
            daily_grp["cum_pnl"] = daily_grp["net_pnl_num"].cumsum()

            g1, g2 = st.columns(2)
            with g1:
                bar_colors = ["#10b981" if v >= 0 else "#ef4444" for v in daily_grp["net_pnl_num"]]
                fig_d = go.Figure(go.Bar(x=daily_grp["date_str"], y=daily_grp["net_pnl_num"], marker_color=bar_colors, name="Daily Net P&L"))
                fig_d.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=290, title="Daily Closed Intraday P&L (₹)", margin=dict(l=8, r=8, t=34, b=8))
                st.plotly_chart(fig_d, use_container_width=True)
            with g2:
                fig_c = go.Figure(go.Scatter(x=daily_grp["date_str"], y=daily_grp["cum_pnl"], mode="lines+markers", name="Cumulative P&L", line=dict(color="#38bdf8", width=2.5)))
                fig_c.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=290, title="Cumulative Closed Intraday P&L (₹)", margin=dict(l=8, r=8, t=34, b=8))
                st.plotly_chart(fig_c, use_container_width=True)

            tot_wins = closed_only[closed_only["net_pnl_num"] > 0]["net_pnl_num"].sum()
            tot_losses = abs(closed_only[closed_only["net_pnl_num"] < 0]["net_pnl_num"].sum())
            win_cnt = int((closed_only["net_pnl_num"] > 0).sum())
            wr_all = (win_cnt / max(len(closed_only), 1)) * 100.0
            pf_all = (tot_wins / tot_losses) if tot_losses > 0 else (99.9 if tot_wins > 0 else 0.0)
            k1, k2, k3 = st.columns(3)
            k1.metric("Closed Trades", f"{len(closed_only)}")
            k2.metric("Win Rate", f"{wr_all:.1f}% ({win_cnt}W)")
            k3.metric("Profit Factor", f"{pf_all:.2f}")

            book_grp = closed_only.groupby("book", as_index=False)["net_pnl_num"].sum()
            fig_b = go.Figure(go.Bar(x=book_grp["book"], y=book_grp["net_pnl_num"], marker_color=["#6366f1", "#f59e0b"], name="Book P&L"))
            fig_b.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=240, title="Net Realized P&L by Book (EQUITY MIS vs FNO)", margin=dict(l=8, r=8, t=34, b=8))
            st.plotly_chart(fig_b, use_container_width=True)

    if not trades_df.empty:
        closed_tod = trades_df[trades_df["status"] != "OPEN"].copy()
        if not closed_tod.empty and "timestamp" in closed_tod.columns:
            closed_tod["net_pnl_num"] = pd.to_numeric(closed_tod["net_pnl"], errors="coerce").fillna(0.0)
            parsed_dt = pd.to_datetime(closed_tod["timestamp"].astype(str).str.replace(" IST", "", regex=False), errors="coerce")
            closed_tod["hour_slot"] = parsed_dt.dt.strftime("%H:00")
            tod_grp = closed_tod.dropna(subset=["hour_slot"]).groupby("hour_slot", sort=True)["net_pnl_num"].sum().reset_index()
            if not tod_grp.empty:
                tod_cols = ["#10b981" if v >= 0 else "#ef4444" for v in tod_grp["net_pnl_num"]]
                fig_tod = go.Figure(go.Bar(x=tod_grp["hour_slot"], y=tod_grp["net_pnl_num"], marker_color=tod_cols, name="Hour Net P&L"))
                fig_tod.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=230, title="Realized Net P&L by Time-of-Day Slot (IST)", margin=dict(l=8, r=8, t=34, b=8))
                st.plotly_chart(fig_tod, use_container_width=True)

            # Pre-Live Graduation Scorecard (100-trade sample gate + Profit Factor >= 1.50)
            c_cnt = len(closed_tod)
            gw = closed_tod[closed_tod["net_pnl_num"] > 0]["net_pnl_num"].sum()
            gl = abs(closed_tod[closed_tod["net_pnl_num"] < 0]["net_pnl_num"].sum())
            pf_cur = (gw / gl) if gl > 0 else (99.9 if gw > 0 else 0.0)
            st.markdown("##### 🎓 Pre-Live Graduation Scorecard (Target: ≥100 Closed Trades & Profit Factor ≥ 1.50)")
            gs1, gs2, gs3 = st.columns(3)
            gs1.progress(min(1.0, c_cnt / 100.0), text=f"Sample Gate: {c_cnt} / 100 Closed Trades")
            gs2.metric("Profit Factor Gate", f"{pf_cur:.2f} / 1.50+", "✅ Pass" if pf_cur >= 1.50 else "⏳ Building")
            gs3.metric("Net Realized P&L", f"₹{closed_tod['net_pnl_num'].sum():+,.2f}")

    st.divider()
    st.markdown("##### 🧪 Offline 5m MIS Replay Sandbox (20% Target Trail + ADX Filter)")
    rb1, rb2, rb3 = st.columns([1.4, 1.1, 1.1])
    with rb1:
        bt_sym = st.selectbox("Replay Stock", ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "TCS.NS", "INFY.NS"], key="bt_sym_sel")
    with rb2:
        bt_days = st.slider("Lookback Days", min_value=5, max_value=25, value=10, key="bt_days_sl")
    with rb3:
        bt_adx = st.slider("Min ADX(14)", min_value=15.0, max_value=32.0, value=22.0, step=1.0, key="bt_adx_sl")

    if st.button("▶ Run 5m Replay Simulation", key="btn_run_5m_bt"):
        res_bt = run_intraday_mis_backtest(symbol=bt_sym, lookback_days=bt_days, min_adx=bt_adx)
        rm1, rm2, rm3, rm4 = st.columns(4)
        rm1.metric("Trades", f"{res_bt['trades_count']}")
        rm2.metric("Win Rate", f"{res_bt['win_rate_pct']:.1f}%")
        rm3.metric("Net P&L", f"₹{res_bt['net_pnl']:+,.2f}")
        rm4.metric("Max Drawdown", f"₹{res_bt['max_drawdown']:,.2f}")
        fig_eq = go.Figure(go.Scatter(y=res_bt["equity_curve"], mode="lines", name="Replay Cash Equity", line=dict(color="#10b981", width=2.5)))
        fig_eq.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=260, title=f"5m Replay Equity Curve — {bt_sym}", margin=dict(l=8, r=8, t=34, b=8))
        st.plotly_chart(fig_eq, use_container_width=True)
        if res_bt["trade_log"]:
            st.dataframe(pd.DataFrame(res_bt["trade_log"]), use_container_width=True, hide_index=True)

with t_ledger:
    l1, l2 = st.columns(2)
    with l1:
        st.markdown("#### 📜 Intraday & F&O Ledger (IST)")
        if not trades_df.empty:
            st.dataframe(trades_df, use_container_width=True, hide_index=True)
        else:
            st.write("No intraday trades logged yet.")
    with l2:
        if lessons_list:
            st.markdown("#### 🧠 Recent Agent Self-Reflection Lessons")
            for les in lessons_list:
                st.caption(f"• {les}")
        if not msgs_df.empty:
            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(
                    "⬇️ Stream CSV",
                    data=msgs_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"intraday_agent_stream_{today_str_ui}.csv",
                    mime="text/csv",
                    key="dl_stream_csv",
                )
            with dl2:
                st.download_button(
                    "⬇️ Stream JSON",
                    data=msgs_df.to_json(orient="records", indent=2).encode("utf-8"),
                    file_name=f"intraday_agent_stream_{today_str_ui}.json",
                    mime="application/json",
                    key="dl_stream_json",
                )
            with dl3:
                lessons_df = msgs_df[msgs_df["msg_type"].astype(str).str.contains("LESSON", case=False, na=False)] if "msg_type" in msgs_df.columns else pd.DataFrame()
                st.download_button(
                    "⬇️ Lessons CSV",
                    data=lessons_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"agent_self_reflection_lessons_{today_str_ui}.csv",
                    mime="text/csv",
                    key="dl_lessons_csv",
                )
        st.markdown("#### 💬 Filterable Intraday 5-Agent Cards")
        msg_filter = st.selectbox(
            "Filter Agent Stream",
            ["ALL", "BUY & EXIT", "TRAILING SL", "AGENT_LESSON", "SKIP & CHOP"],
            key="msg_stream_filter",
        )
        filtered_msgs = msgs_df.copy()
        if not filtered_msgs.empty and "msg_type" in filtered_msgs.columns and msg_filter != "ALL":
            mt_s = filtered_msgs["msg_type"].astype(str)
            sum_s = filtered_msgs["summary"].astype(str)
            if msg_filter == "BUY & EXIT":
                filtered_msgs = filtered_msgs[mt_s.str.contains("BUY|TARGET|SL_HIT|SQUAREOFF|FLOOR|MANUAL", case=False, na=False)]
            elif msg_filter == "TRAILING SL":
                filtered_msgs = filtered_msgs[mt_s.str.contains("TRAIL", case=False, na=False)]
            elif msg_filter == "AGENT_LESSON":
                filtered_msgs = filtered_msgs[mt_s.str.contains("LESSON", case=False, na=False)]
            elif msg_filter == "SKIP & CHOP":
                filtered_msgs = filtered_msgs[sum_s.str.contains("SKIP|CHOP", case=False, na=False)]

        for _, r in filtered_msgs.head(20).iterrows():
            st.markdown(
                f"<div class='panel-box' style='margin-bottom:8px;padding:10px;'>"
                f"<div style='font-size:12px;color:#94a3b8;'><b>{r.get('timestamp','')}</b> • <b>{r.get('sender','')}</b> • <code>{r.get('msg_type','')}</code></div>"
                f"<div style='margin-top:4px;'>{r.get('summary','')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
