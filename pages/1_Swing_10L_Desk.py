import streamlit as st
import streamlit.components.v1 as components
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import swing_engine as _se
from swing_engine import get_gsheet_client, SWING_SHEET_NAME
from intraday_engine import compute_book_and_combined_trust
from swing_desk_helpers import (
    INIT_SWING_CAPITAL,
    MAX_SWING_SLOTS,
    MAX_SWING_PER_SECTOR,
    read_swing_account_state,
    ensure_swing_audit_columns,
    compute_swing_open_downside_risk,
    render_circular_trust_badge,
)
from skills.market_skills import get_sector, get_sector_counts, compute_adx_series, compute_rsi_series, compute_pdh_pdl
from backtest import run_historical_backtest

IST = timezone(timedelta(hours=5, minutes=30))

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
</style>
""", unsafe_allow_html=True)


def tier_pill(t: str) -> str:
    u = str(t).upper()
    if "LOW" in u:
        return "<span style='background:rgba(16,185,129,0.16);color:#34d399;border:1px solid rgba(16,185,129,0.35);padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>LOW</span>"
    if "HIGH" in u:
        return "<span style='background:rgba(239,68,68,0.18);color:#f87171;border:1px solid rgba(239,68,68,0.38);padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>HIGH</span>"
    return "<span style='background:rgba(245,158,11,0.16);color:#fbbf24;border:1px solid rgba(245,158,11,0.35);padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>MID</span>"


def prot_pill(entry: float, sl: float, qty: int) -> str:
    if sl > entry:
        lk = round((sl - entry) * qty, 0)
        return f"<span style='background:rgba(16,185,129,0.18);color:#34d399;border:1px solid rgba(16,185,129,0.4);padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>Locked +₹{lk:,.0f}</span>"
    return "<span style='background:rgba(239,68,68,0.18);color:#f87171;border:1px solid rgba(239,68,68,0.4);padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>At risk</span>"


api_key = st.secrets.get("GEMINI_API_KEY", "")
if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()


@st.cache_data(ttl=90, show_spinner=False)
def load_swing_snapshot(_gcp_creds: dict):
    sh = get_gsheet_client(_gcp_creds, SWING_SHEET_NAME)
    trades_ws = sh.worksheet("trades")
    ensure_swing_audit_columns(trades_ws, backfill_missing=False)
    t_rec = trades_ws.get_all_records()
    try:
        m_rec = sh.worksheet("agent_messages").get_all_records()
    except Exception:
        m_rec = []
    acct_state = read_swing_account_state(sh, default_cash=INIT_SWING_CAPITAL)
    return acct_state["cash"], acct_state["peak_floor_ref"], t_rec, m_rec


@st.cache_data(ttl=90, show_spinner=False)
def fetch_swing_ltps(syms: list[str]) -> dict[str, float]:
    if not syms:
        return {}
    out = {}
    try:
        raw = yf.download(syms, period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        for s in syms:
            sub = raw[s]["Close"].dropna() if len(syms) > 1 else raw["Close"].dropna()
            if not sub.empty:
                out[s] = round(float(sub.iloc[-1]), 2)
    except Exception:
        pass
    return out


@st.cache_data(ttl=120, show_spinner=False)
def fetch_nifty_bench(period: str = "10d") -> pd.Series:
    try:
        df = yf.Ticker("^NSEI").history(period=period, interval="1d")
        if df is not None and not df.empty:
            s = df["Close"].dropna()
            return (s / float(s.iloc[0])) * INIT_SWING_CAPITAL
    except Exception:
        pass
    return pd.Series(dtype=float)


now_ist_dt = datetime.now(timezone.utc).astimezone(IST)
sw_cash, peak_floor_ref, sw_trades, sw_msgs = load_swing_snapshot(gcp_creds)
sw_df = pd.DataFrame(sw_trades) if sw_trades else pd.DataFrame()
msgs_df = pd.DataFrame(sw_msgs).iloc[::-1] if sw_msgs else pd.DataFrame()

open_sw = sw_df[sw_df["status"] == "OPEN"].copy() if not sw_df.empty else pd.DataFrame()
closed_sw = sw_df[sw_df["status"] != "OPEN"].copy() if not sw_df.empty else pd.DataFrame()
ltps = fetch_swing_ltps(open_sw["symbol"].astype(str).tolist() if not open_sw.empty else [])

holdings_val = 0.0
unreal_pnl = 0.0
watch_items = []
for _, r in open_sw.iterrows():
    s = str(r["symbol"])
    q = int(float(r["qty"]))
    e = float(r["entry"])
    sl = float(r["sl"])
    tp = float(r["target"])
    ltp = float(ltps.get(s, e))
    mtm = round((ltp - e) * q, 2)
    prog = round(((ltp - e) / max(tp - e, 0.5)) * 100.0, 1)
    holdings_val += ltp * q
    unreal_pnl += mtm
    watch_items.append({
        "symbol": s, "sector": get_sector(s), "tier": str(r.get("tier", "MID")),
        "qty": q, "entry": e, "ltp": ltp, "sl": sl, "target": tp, "prog": prog, "mtm": mtm,
    })

realized_pnl = float(pd.to_numeric(closed_sw["pnl"], errors="coerce").fillna(0.0).sum()) if not closed_sw.empty else 0.0
total_equity = round(sw_cash + holdings_val, 2)
eq_chg_pct = ((total_equity - INIT_SWING_CAPITAL) / INIT_SWING_CAPITAL) * 100.0
open_stop_risk = compute_swing_open_downside_risk(open_sw)
trust_map = compute_book_and_combined_trust([], sw_trades)
sec_counts = get_sector_counts(open_sw["symbol"].astype(str).tolist() if not open_sw.empty else [])

st.markdown("## 📈 Desk 1: ₹10,00,000 Positional Equity Swing Terminal (V4.7)")
st.caption(f"250-Stock NSE Universe • IST: **{now_ist_dt.strftime('%I:%M:%S %p')}** • Slot Cap: **{MAX_SWING_SLOTS}** • Sector Cap: **{MAX_SWING_PER_SECTOR}/sector**")

ac1, ac2, ac3, ac4 = st.columns([1.4, 1.2, 1.3, 1.1])
with ac1:
    if st.button("⚡ Run 250-Stock Swing Cycle", use_container_width=True, type="primary"):
        runner = getattr(_se, "run_full_cycle", None) or getattr(_se, "run_swing_cycle", None)
        if runner is not None:
            with st.spinner("Running Swing Cycle..."):
                if asyncio.iscoroutinefunction(runner):
                    asyncio.run(runner(api_key, gcp_creds))
                else:
                    runner(api_key, gcp_creds)
            st.cache_data.clear()
            st.success("Swing cycle completed.")
            st.rerun()
        else:
            st.info("On-demand Swing runner (`run_swing_cycle`) not found in `swing_engine.py`; scheduled workflow remains active.")
with ac2:
    if st.button("🔄 Refresh Prices", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with ac3:
    if st.button("🧾 Sync Audit Columns", use_container_width=True):
        sh_sync = get_gsheet_client(gcp_creds, SWING_SHEET_NAME)
        ensure_swing_audit_columns(sh_sync.worksheet("trades"), backfill_missing=True)
        st.cache_data.clear()
        st.success("Synced `side`, `initial_sl`, `gross_pnl`, `charges_slippage` on Swing Ledger.")
        st.rerun()
with ac4:
    auto_ref = st.toggle("Auto-refresh (60s)", value=False, key="sw_auto_ref")
    if auto_ref:
        components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 60000);</script>", height=0)

slot_badge = "⚠️ Over Cap (Paused)" if len(open_sw) >= MAX_SWING_SLOTS else "✅ Active"
st.markdown(
    f"<div class='panel-box' style='margin-bottom:10px;display:flex;flex-wrap:wrap;gap:14px;font-size:13px;'>"
    f"<span><b>Slots:</b> {len(open_sw)}/{MAX_SWING_SLOTS} ({slot_badge})</span>"
    f"<span><b>Free Cash:</b> ₹{sw_cash:,.2f}</span>"
    f"<span><b>Gross Deployed:</b> ₹{holdings_val:,.2f}</span>"
    f"<span><b>Peak Floor Ref:</b> ₹{peak_floor_ref:,.2f}</span>"
    f"</div>",
    unsafe_allow_html=True,
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Swing Equity", f"₹{total_equity:,.2f}", f"{eq_chg_pct:+.2f}%")
k2.metric("Unrealized MTM", f"₹{unreal_pnl:+,.2f}", f"Open Stop Risk: ₹{open_stop_risk:,.2f}")
k3.metric("Realized P&L", f"₹{realized_pnl:+,.2f}", f"Closed: {len(closed_sw)}")
k4.metric("Gross Deployed", f"₹{holdings_val:,.2f}", f"Cash: ₹{sw_cash:,.0f}")

# Circular Trust Icons Row (50-Point Baseline + Delta Badges)
rc1, rc2, rc3, rc4 = st.columns(4)
with rc1:
    st.markdown(render_circular_trust_badge("AG1", "Low Risk", trust_map["SWING"]["AG1_LOW_RISK"], "Steady trend & volume"), unsafe_allow_html=True)
with rc2:
    st.markdown(render_circular_trust_badge("AG2", "Mid Risk", trust_map["SWING"]["AG2_MID_RISK"], "Momentum & breakout"), unsafe_allow_html=True)
with rc3:
    st.markdown(render_circular_trust_badge("AG3", "High Risk", trust_map["SWING"]["AG3_HIGH_RISK"], "Regime-gated high beta"), unsafe_allow_html=True)
with rc4:
    st.markdown(render_circular_trust_badge("AG4", "Chief Analyst", 50, "Trust-weighted auditor"), unsafe_allow_html=True)

if sec_counts:
    sec_pills = " &nbsp; ".join([f"<b>{sec}:</b> {cnt}/{MAX_SWING_PER_SECTOR}" for sec, cnt in sorted(sec_counts.items())])
    st.markdown(f"<div class='panel-box' style='margin-top:10px;margin-bottom:10px;'>🏭 <b>Sector Concentration:</b> &nbsp; {sec_pills}</div>", unsafe_allow_html=True)

tab_cc, tab_ch, tab_p, tab_a, tab_l, tab_b = st.tabs([
    "🎛️ Command Center",
    "📈 Pro Live Charts",
    "💼 Portfolio & P&L",
    "🧭 Agents & Trust",
    "📜 Ledger & Stream",
    "🧪 Backtest & Live Broker",
])

with tab_cc:
    c_left, c_right = st.columns([2.2, 1.0])
    with c_left:
        st.markdown("#### 📈 Swing Portfolio vs. NIFTY 50 Benchmark")
        bench_s = fetch_nifty_bench("10d")
        fig_sw = go.Figure()
        if not bench_s.empty:
            fig_sw.add_trace(go.Scatter(x=bench_s.index, y=bench_s.values, mode="lines", name="NIFTY 50 (₹10L Eq.)", line=dict(color="#94a3b8", width=2, dash="dash")))
        fig_sw.add_trace(go.Scatter(
            x=[now_ist_dt - timedelta(days=2), now_ist_dt],
            y=[INIT_SWING_CAPITAL, total_equity],
            mode="lines+markers",
            name="Swing Portfolio (₹)",
            line=dict(color="#10b981", width=3),
        ))
        fig_sw.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=250, margin=dict(l=8, r=8, t=20, b=8))
        st.plotly_chart(fig_sw, use_container_width=True)
    with c_right:
        st.markdown("#### 🧠 Self-Reflection Lessons")
        les_df_cc = msgs_df[msgs_df["msg_type"].astype(str).str.contains("LESSON", case=False, na=False)] if (not msgs_df.empty and "msg_type" in msgs_df.columns) else pd.DataFrame()
        if not les_df_cc.empty:
            for _, lr in les_df_cc.head(5).iterrows():
                st.caption(f"• {lr.get('summary','')}")
        else:
            st.info("Post-trade lessons appear here when a swing trade exits.")

    st.markdown("#### 💬 Live Agent Stream (IST)")
    if not msgs_df.empty:
        s_cols = st.columns(5)
        for idx_s, ag_tag in enumerate(["AG1", "AG2", "AG3", "AG4", "AG5"]):
            with s_cols[idx_s]:
                sub_m = msgs_df[msgs_df["sender"].astype(str).str.contains(ag_tag, case=False, na=False)] if "sender" in msgs_df.columns else pd.DataFrame()
                st.markdown(f"<div class='panel-box'><b>{ag_tag}</b></div>", unsafe_allow_html=True)
                if not sub_m.empty:
                    for _, mr in sub_m.head(2).iterrows():
                        st.caption(f"• {str(mr.get('summary',''))[:110]}")
                else:
                    st.caption("Idle / waiting")

with tab_ch:
    st.markdown("#### 📈 Pro NSE Terminal Chart (Daily Candles + EMAs + PDH/PDL + RSI(14) + ADX)")
    open_syms = open_sw["symbol"].astype(str).tolist() if not open_sw.empty else ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    pick_sw = st.selectbox("Select Swing Stock", open_syms, key="sw_chart_pick")
    df_ch = yf.Ticker(pick_sw).history(period="6mo", interval="1d")
    if df_ch is not None and not df_ch.empty:
        df_ch = df_ch.dropna().copy()
        df_ch["EMA20"] = df_ch["Close"].ewm(span=20).mean()
        df_ch["EMA50"] = df_ch["Close"].ewm(span=50).mean()
        df_ch["ADX14"] = compute_adx_series(df_ch["High"], df_ch["Low"], df_ch["Close"], period=14)
        df_ch["RSI14"] = compute_rsi_series(df_ch["Close"], period=14)
        pd_lv = compute_pdh_pdl(df_ch)

        fig_c1 = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.58, 0.21, 0.21])
        fig_c1.add_trace(go.Candlestick(x=df_ch.index, open=df_ch["Open"], high=df_ch["High"], low=df_ch["Low"], close=df_ch["Close"], name=pick_sw), row=1, col=1)
        fig_c1.add_trace(go.Scatter(x=df_ch.index, y=df_ch["EMA20"], mode="lines", name="EMA 20", line=dict(color="#38bdf8", width=1.4)), row=1, col=1)
        fig_c1.add_trace(go.Scatter(x=df_ch.index, y=df_ch["EMA50"], mode="lines", name="EMA 50", line=dict(color="#f59e0b", width=1.4)), row=1, col=1)
        if pd_lv["pdh"] is not None:
            fig_c1.add_hline(y=pd_lv["pdh"], line_dash="dot", line_color="#94a3b8", annotation_text=f"PDH ₹{pd_lv['pdh']:,.2f}", row=1, col=1)
        if pd_lv["pdl"] is not None:
            fig_c1.add_hline(y=pd_lv["pdl"], line_dash="dot", line_color="#64748b", annotation_text=f"PDL ₹{pd_lv['pdl']:,.2f}", row=1, col=1)
        fig_c1.add_trace(go.Scatter(x=df_ch.index, y=df_ch["RSI14"], mode="lines", name="RSI(14)", line=dict(color="#a855f7", width=1.5)), row=2, col=1)
        fig_c1.add_hline(y=72.0, line_dash="dot", line_color="#f87171", row=2, col=1)
        fig_c1.add_trace(go.Scatter(x=df_ch.index, y=df_ch["ADX14"], mode="lines", name="ADX(14)", line=dict(color="#eab308", width=1.6)), row=3, col=1)
        fig_c1.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=520, margin=dict(l=8, r=8, t=20, b=8), xaxis_rangeslider_visible=False)
        st.plotly_chart(fig_c1, use_container_width=True)

with tab_p:
    st.markdown("#### 🎯 Active Swing Positions & Trailing Protection")
    flt_sw = st.radio("Filter Positions", ["All", "Locked Profit Only", "At Risk Only"], horizontal=True, key="sw_watch_filter")
    disp_sw = watch_items
    if flt_sw == "Locked Profit Only":
        disp_sw = [w for w in watch_items if w["sl"] > w["entry"]]
    elif flt_sw == "At Risk Only":
        disp_sw = [w for w in watch_items if w["sl"] <= w["entry"]]

    if disp_sw:
        html_sw = [
            "<div class='panel-box' style='padding:14px;'><div style='overflow-x:auto;'>",
            "<table style='width:100%;border-collapse:collapse;font-size:13px;'>",
            "<thead><tr style='border-bottom:1px solid #1e293b;color:#94a3b8;font-size:11px;text-transform:uppercase;'>",
            "<th style='text-align:left;padding:8px;'>Symbol</th><th style='text-align:left;padding:8px;'>Sector</th><th style='text-align:left;padding:8px;'>Tier</th>",
            "<th style='text-align:right;padding:8px;'>Qty</th><th style='text-align:right;padding:8px;'>Entry ₹</th><th style='text-align:right;padding:8px;'>LTP ₹</th>",
            "<th style='text-align:right;padding:8px;'>Stop ₹</th><th style='text-align:right;padding:8px;'>Target ₹</th><th style='text-align:right;padding:8px;'>Prog %</th>",
            "<th style='text-align:center;padding:8px;'>Protection</th><th style='text-align:right;padding:8px;'>MTM P&L ₹</th></tr></thead><tbody>",
        ]
        for w in disp_sw:
            mc = "#34d399" if w["mtm"] >= 0 else "#f87171"
            ms = f"+{w['mtm']:,.2f}" if w["mtm"] > 0 else f"{w['mtm']:,.2f}"
            pc = "#34d399" if w["prog"] >= 20 else ("#f87171" if w["prog"] < 0 else "#cbd5e1")
            html_sw.append(
                f"<tr style='border-bottom:1px solid rgba(30,41,59,0.65);'>"
                f"<td style='padding:9px;font-weight:700;'>{w['symbol'].replace('.NS','')}</td>"
                f"<td style='padding:9px;color:#94a3b8;'>{w['sector']}</td>"
                f"<td style='padding:9px;'>{tier_pill(w['tier'])}</td>"
                f"<td style='padding:9px;text-align:right;'>{w['qty']:,}</td>"
                f"<td style='padding:9px;text-align:right;'>{w['entry']:,.2f}</td>"
                f"<td style='padding:9px;text-align:right;font-weight:600;'>{w['ltp']:,.2f}</td>"
                f"<td style='padding:9px;text-align:right;'>{w['sl']:,.2f}</td>"
                f"<td style='padding:9px;text-align:right;'>{w['target']:,.2f}</td>"
                f"<td style='padding:9px;text-align:right;font-weight:600;color:{pc};'>{w['prog']:+.1f}%</td>"
                f"<td style='padding:9px;text-align:center;'>{prot_pill(w['entry'], w['sl'], w['qty'])}</td>"
                f"<td style='padding:9px;text-align:right;font-weight:700;color:{mc};'>{ms}</td></tr>"
            )
        html_sw.append("</tbody></table></div></div>")
        st.markdown("".join(html_sw), unsafe_allow_html=True)

with tab_a:
    st.markdown("#### 🧭 Agent Trust Leaderboard (50-Point Baseline)")
    t_df = pd.DataFrame([
        {"Agent": "AG1_LOW_RISK", "Swing Trust": trust_map["SWING"]["AG1_LOW_RISK"], "Combined Trust": trust_map["COMBINED"]["AG1_LOW_RISK"]},
        {"Agent": "AG2_MID_RISK", "Swing Trust": trust_map["SWING"]["AG2_MID_RISK"], "Combined Trust": trust_map["COMBINED"]["AG2_MID_RISK"]},
        {"Agent": "AG3_HIGH_RISK", "Swing Trust": trust_map["SWING"]["AG3_HIGH_RISK"], "Combined Trust": trust_map["COMBINED"]["AG3_HIGH_RISK"]},
    ])
    st.dataframe(t_df, use_container_width=True, hide_index=True)

with tab_l:
    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("#### 📜 Swing Trade Ledger")
        if not sw_df.empty:
            st.dataframe(sw_df.iloc[::-1], use_container_width=True, hide_index=True)
        else:
            st.info("No swing trades found.")
    with c_right:
        st.markdown("#### 💬 Inter-Agent Message Stream (IST)")
        if not msgs_df.empty:
            td_s = now_ist_dt.strftime("%Y-%m-%d")
            d1, d2 = st.columns(2)
            with d1:
                st.download_button("⬇️ Stream CSV", data=msgs_df.to_csv(index=False).encode("utf-8"), file_name=f"swing_stream_{td_s}.csv", mime="text/csv", key="sw_dl_csv")
            with d2:
                les_df = msgs_df[msgs_df["msg_type"].astype(str).str.contains("LESSON", case=False, na=False)] if "msg_type" in msgs_df.columns else pd.DataFrame()
                st.download_button("⬇️ Lessons CSV", data=les_df.to_csv(index=False).encode("utf-8"), file_name=f"swing_lessons_{td_s}.csv", mime="text/csv", key="sw_dl_les")
            for _, mr in msgs_df.head(20).iterrows():
                st.markdown(
                    f"<div class='panel-box' style='margin-bottom:8px;padding:10px;'>"
                    f"<div style='font-size:12px;color:#94a3b8;'><b>{mr.get('timestamp','')}</b> • <b>{mr.get('sender','')}</b> • <code>{mr.get('msg_type','')}</code></div>"
                    f"<div style='margin-top:4px;'>{mr.get('summary','')}</div></div>",
                    unsafe_allow_html=True,
                )

with tab_b:
    st.markdown("#### 🧪 Historical Swing Backtester (₹10L Positional Rules)")
    sb1, sb2, sb3 = st.columns([1.2, 1.2, 1.2])
    with sb1:
        sw_bt_months = st.slider("Lookback Months", min_value=2, max_value=12, value=6, key="sw_bt_months")
    with sb2:
        sw_bt_univ = st.selectbox("Universe Size", [50, 100, 150], index=1, key="sw_bt_univ")
    with sb3:
        sw_bt_slots = st.slider("Max Open Slots", min_value=5, max_value=15, value=15, key="sw_bt_slots")

    if st.button("▶ Run Swing Historical Backtest", key="btn_run_sw_bt"):
        with st.spinner("Replaying historical NSE daily bars..."):
            res_sw_bt = run_historical_backtest(
                lookback_months=sw_bt_months,
                initial_capital=INIT_SWING_CAPITAL,
                max_open_positions=sw_bt_slots,
                universe_size=sw_bt_univ,
            )
        if "error" in res_sw_bt:
            st.warning(res_sw_bt["error"])
        else:
            sm = res_sw_bt["metrics"]
            m_c1, m_c2, m_c3, m_c4 = st.columns(4)
            m_c1.metric("Final Equity", f"₹{sm['final_equity']:,.2f}", f"{sm['net_return_pct']:+.2f}%")
            m_c2.metric("Alpha vs NIFTY", f"{sm['alpha_pct']:+.2f}%", f"NIFTY {sm['nifty_return_pct']:+.2f}%")
            m_c3.metric("Win Rate / PF", f"{sm['win_rate_pct']:.1f}%", f"PF {sm['profit_factor']:.2f}")
            m_c4.metric("Max Drawdown / Sharpe", f"{sm['max_drawdown_pct']:.2f}%", f"Sharpe {sm['sharpe_ratio']:.2f}")
            eq_curve_df = res_sw_bt["equity_curve"]
            if eq_curve_df is not None and not eq_curve_df.empty:
                fig_sbt = go.Figure()
                fig_sbt.add_trace(go.Scatter(x=eq_curve_df["date"], y=eq_curve_df["portfolio_equity"], mode="lines", name="Swing Backtest Equity", line=dict(color="#10b981", width=2.5)))
                fig_sbt.add_trace(go.Scatter(x=eq_curve_df["date"], y=eq_curve_df["nifty_benchmark"], mode="lines", name="NIFTY Benchmark", line=dict(color="#94a3b8", width=2, dash="dash")))
                fig_sbt.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=280, margin=dict(l=8, r=8, t=24, b=8))
                st.plotly_chart(fig_sbt, use_container_width=True)
            ct_df = res_sw_bt["closed_trades"]
            if ct_df is not None and not ct_df.empty:
                st.dataframe(ct_df.tail(25), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### 🔒 Live Broker Execution Status")
    st.info("Live broker order execution and static-IP infrastructure remain parked until paper/replay testing passes your graduation gates.")
