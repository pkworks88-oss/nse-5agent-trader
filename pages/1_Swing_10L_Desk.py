import streamlit as st
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from backtest import run_historical_backtest
from broker_adapter import get_broker_adapter
from swing_engine import (
    SWING_SHEET_NAME,
    MAX_OPEN_POSITIONS,
    run_full_cycle,
    get_gsheet_client,
    now_ist,
    compute_agent_trust_scores,
    infer_trade_agent,
    record_trade_reflection,
    get_persisted_peak_pnl,
)

IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


st.set_page_config(page_title="Desk 1: ₹10L Positional Swing", page_icon="📈", layout="wide")

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    .wf-card { border-radius: 12px; padding: 12px 8px; text-align: center; background: #0b1426; min-height: 138px; }
    .wf-green { border: 1.5px solid #10b981; }
    .wf-blue  { border: 1.5px solid #3b82f6; }
    .wf-orange{ border: 1.5px solid #f97316; }
    .wf-purple{ border: 1.5px solid #a855f7; }
    .wf-teal  { border: 1.5px solid #06b6d4; }
    .wf-title { font-size: 16px; font-weight: 800; color: #ffffff; }
    .wf-sub   { font-size: 11px; color: #94a3b8; }
    .trust-pill { display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; background: #132342; color: #38bdf8; }
    .agent-col { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 12px; min-height: 270px; }
    .chat-bubble { background: #131f38; border-left: 3px solid #38bdf8; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 12px; }
    .lesson-card { background: #111b33; border-left: 3px solid #f59e0b; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; font-size: 12px; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def get_live_ltps(symbols: tuple[str, ...]) -> dict[str, float]:
    prices = {}
    if not symbols:
        return prices
    try:
        data = yf.download(list(symbols), period="2d", interval="15m", group_by="ticker", progress=False, threads=True)
        for sym in symbols:
            try:
                sub = data[sym]["Close"].dropna() if len(symbols) > 1 else data["Close"].dropna()
                if not sub.empty:
                    prices[sym] = round(float(sub.iloc[-1]), 2)
            except Exception:
                continue
    except Exception:
        pass
    return prices


@st.cache_data(ttl=60)
def get_stock_chart_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        return df.dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=180)
def get_nifty_benchmark_series(start_date_str: str) -> pd.DataFrame:
    try:
        start_dt = pd.to_datetime(start_date_str) - timedelta(days=2)
        df = yf.Ticker("^NSEI").history(start=start_dt.strftime("%Y-%m-%d"), interval="1d")
        return df.dropna()
    except Exception:
        return pd.DataFrame()


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

sh = get_gsheet_client(gcp_creds, SWING_SHEET_NAME)
acct_ws = sh.worksheet("account")
cash = float(acct_ws.acell("A2").value or 1_000_000.0)
persisted_peak = get_persisted_peak_pnl(acct_ws)
trades_records = sh.worksheet("trades").get_all_records()
msgs_records = sh.worksheet("agent_messages").get_all_records()
trust_scores = compute_agent_trust_scores(trades_records)

trades_df = pd.DataFrame(trades_records).iloc[::-1] if trades_records else pd.DataFrame()
msgs_df = pd.DataFrame(msgs_records).iloc[::-1].head(150) if msgs_records else pd.DataFrame()

open_t = trades_df[trades_df["status"] == "OPEN"].copy() if not trades_df.empty else pd.DataFrame()
closed_t = trades_df[trades_df["status"] != "OPEN"].copy() if not trades_df.empty else pd.DataFrame()

open_symbols_tuple = tuple(sorted(open_t["symbol"].unique().tolist())) if not open_t.empty else ()
live_ltp_map = get_live_ltps(open_symbols_tuple)

if not open_t.empty:
    open_t["qty"] = open_t["qty"].astype(float)
    open_t["entry"] = open_t["entry"].astype(float)
    open_t["sl"] = open_t["sl"].astype(float)
    open_t["target"] = open_t["target"].astype(float)
    open_t["live_ltp"] = open_t["symbol"].map(live_ltp_map).fillna(open_t["entry"])
    open_t["unrealized_pnl"] = ((open_t["live_ltp"] - open_t["entry"]) * open_t["qty"]).round(2)
    open_t["pnl_pct"] = (((open_t["live_ltp"] - open_t["entry"]) / open_t["entry"]) * 100).round(2)
    current_holdings_val = float((open_t["qty"] * open_t["live_ltp"]).sum())
    unrealized_pnl_total = float(open_t["unrealized_pnl"].sum())
else:
    current_holdings_val = 0.0
    unrealized_pnl_total = 0.0

realized_pnl = float(closed_t["pnl"].astype(float).sum()) if not closed_t.empty else 0.0
total_portfolio = cash + current_holdings_val
overall_ret_pct = ((total_portfolio - 1_000_000.0) / 1_000_000.0) * 100.0

st.markdown(f"## 📈 Desk 1: ₹10,00,000 Positional Equity Swing Terminal (V3.1)")
c1, c2, c3, c4 = st.columns([1.4, 1.1, 1.2, 2.3])
with c1:
    if st.button("⚡ Run 250-Stock Swing Cycle", use_container_width=True, type="primary"):
        with st.spinner("Scanning 250 NSE stocks + V3.1 Guardrails..."):
            asyncio.run(run_full_cycle(api_key, gcp_creds))
        st.cache_data.clear()
        st.rerun()
with c2:
    if st.button("🔄 Refresh Prices", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with c3:
    auto_refresh = st.toggle("⏱️ Auto-Refresh (60s)", value=False)
with c4:
    st.markdown(
        f"<div style='text-align:right;padding-top:6px;font-size:13px;color:#94a3b8;'>"
        f"Equity: <b style='color:#f8fafc;'>₹{total_portfolio:,.2f}</b> ({overall_ret_pct:+.2f}%) &nbsp;|&nbsp; "
        f"Open: <b>{len(open_t)}/{MAX_OPEN_POSITIONS}</b> &nbsp;|&nbsp; Peak Floor Ref: <b>₹{persisted_peak:+,.0f}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )

ag1_msgs = msgs_df[msgs_df["sender"] == "AG1_LOW_RISK"].head(3).to_dict("records") if not msgs_df.empty else []
ag2_msgs = msgs_df[msgs_df["sender"] == "AG2_MID_RISK"].head(3).to_dict("records") if not msgs_df.empty else []
ag3_msgs = msgs_df[msgs_df["sender"] == "AG3_HIGH_RISK"].head(3).to_dict("records") if not msgs_df.empty else []
ag4_msgs = msgs_df[msgs_df["sender"] == "AG4_CHIEF_ANALYST"].head(3).to_dict("records") if not msgs_df.empty else []
ag5_msgs = msgs_df[msgs_df["sender"] == "AG5_EXECUTION_BROKER"].head(3).to_dict("records") if not msgs_df.empty else []
lesson_msgs = msgs_df[msgs_df["msg_type"] == "AGENT_LESSON"].head(5).to_dict("records") if not msgs_df.empty else []

tab_dash, tab_charts, tab_port, tab_agents, tab_logs, tab_bt_live = st.tabs([
    "🏠 Command Center",
    "📈 Pro Live Charts",
    "💼 Portfolio & P&L",
    "🤖 Agents & Trust",
    "📜 Ledger",
    "🧪 Backtest & Live Broker",
])

with tab_dash:
    w1, w2, w3, w4, w5 = st.columns(5)
    t1, t2, t3 = trust_scores["AG1_LOW_RISK"]["score"], trust_scores["AG2_MID_RISK"]["score"], trust_scores["AG3_HIGH_RISK"]["score"]
    with w1:
        st.markdown(f"<div class='wf-card wf-green'><div>🛡️</div><div class='wf-title'>AG1</div><div class='wf-sub'>Low Risk</div><span class='trust-pill'>Trust {t1}/100</span></div>", unsafe_allow_html=True)
    with w2:
        st.markdown(f"<div class='wf-card wf-blue'><div>📈</div><div class='wf-title'>AG2</div><div class='wf-sub'>Mid Risk</div><span class='trust-pill'>Trust {t2}/100</span></div>", unsafe_allow_html=True)
    with w3:
        st.markdown(f"<div class='wf-card wf-orange'><div>🚀</div><div class='wf-title'>AG3</div><div class='wf-sub'>High Risk (Regime-Gated)</div><span class='trust-pill'>Trust {t3}/100</span></div>", unsafe_allow_html=True)
    with w4:
        st.markdown("<div class='wf-card wf-purple'><div>🧠</div><div class='wf-title'>AG4</div><div class='wf-sub'>Chief Analyst</div><span class='trust-pill'>Trust-Weighted</span></div>", unsafe_allow_html=True)
    with w5:
        st.markdown(f"<div class='wf-card wf-teal'><div>🛒</div><div class='wf-title'>AG5</div><div class='wf-sub'>Watchdog & 7d Time-Stop</div><span class='trust-pill'>{len(open_t)}/{MAX_OPEN_POSITIONS} Open</span></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    b_col, l_col = st.columns([1.8, 1.2])
    with b_col:
        st.markdown("#### 📈 Swing Portfolio vs. NIFTY 50 Benchmark")
        if trades_records:
            chron = pd.DataFrame(trades_records)
            chron["dt"] = pd.to_datetime(chron["timestamp"].str.replace(" IST", "", regex=False), errors="coerce")
            chron = chron.dropna(subset=["dt"]).sort_values("dt")
            nifty_hist = get_nifty_benchmark_series(chron["dt"].min().strftime("%Y-%m-%d"))

            eq_dates = [chron["dt"].min() - timedelta(hours=1)]
            eq_vals = [1_000_000.0]
            run_r = 0.0
            for _, tr in chron.iterrows():
                if tr.get("status") != "OPEN":
                    run_r += float(tr.get("pnl", 0) or 0)
                    eq_dates.append(tr["dt"])
                    eq_vals.append(1_000_000.0 + run_r)
            eq_dates.append(current_ist.replace(tzinfo=None))
            eq_vals.append(total_portfolio)

            fig_b = go.Figure()
            fig_b.add_trace(go.Scatter(x=eq_dates, y=eq_vals, mode="lines+markers", name="Swing Portfolio (₹)", line=dict(color="#10b981", width=3)))
            if not nifty_hist.empty:
                n_idx = nifty_hist.index.tz_localize(None)
                scaled_n = (nifty_hist["Close"] / float(nifty_hist["Close"].iloc[0])) * 1_000_000.0
                fig_b.add_trace(go.Scatter(x=n_idx, y=scaled_n, mode="lines", name="NIFTY 50 (₹10L Eq.)", line=dict(color="#94a3b8", width=2, dash="dash")))
            fig_b.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=250, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_b, use_container_width=True)

    with l_col:
        st.markdown("#### 🧠 Self-Reflection Lessons")
        if not lesson_msgs:
            st.markdown("<div class='panel-box' style='min-height:250px;'><div class='lesson-card'>Post-trade lessons automatically log here whenever any swing trade exits.</div></div>", unsafe_allow_html=True)
        else:
            html_l = "<div class='panel-box' style='min-height:250px;max-height:250px;overflow-y:auto;'>"
            for lm in lesson_msgs:
                html_l += f"<div class='lesson-card'>🎓 {lm['summary']}</div>"
            html_l += "</div>"
            st.markdown(html_l, unsafe_allow_html=True)

    st.markdown("#### 💬 Live Agent Stream (IST)")
    cols = st.columns(5)
    titles = [("🛡️ AG1", "#10b981", ag1_msgs), ("📈 AG2", "#3b82f6", ag2_msgs), ("🚀 AG3", "#f97316", ag3_msgs), ("🧠 AG4", "#a855f7", ag4_msgs), ("⚡ AG5", "#06b6d4", ag5_msgs)]
    for col_o, (ttl, clr, recs) in zip(cols, titles):
        with col_o:
            inner = "".join([f"<div class='chat-bubble' style='border-left-color:{clr};'>{r['summary']}</div>" for r in recs]) or "<div class='chat-bubble'>Standing by...</div>"
            st.markdown(f"<div class='agent-col' style='border-top:3px solid {clr};'><b>{ttl}</b><br><br>{inner}</div>", unsafe_allow_html=True)

# ==============================================================================
# PRO LIVE CHARTS (V3.1: NO OVERNIGHT GAPS + AUTO-FRAMED SL/TP + VWAP/EMA/RSI)
# ==============================================================================
with tab_charts:
    st.markdown("#### 📈 Pro NSE Terminal Chart (Gap-Free Hours + VWAP, EMA 20/50, Volume & RSI-14)")
    portfolio_syms = open_t["symbol"].tolist() if not open_t.empty else []
    default_syms = ["NIFTY 50 (^NSEI)", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]
    selectable_stocks = list(dict.fromkeys(portfolio_syms + default_syms))

    ch1, ch2, ch3, ch4 = st.columns([1.8, 1.4, 1.1, 1.3])
    with ch1:
        selected_label = st.selectbox("Select Stock (Holdings First)", selectable_stocks)
        chart_sym = "^NSEI" if "NIFTY 50" in selected_label else selected_label
    with ch2:
        tf_choice = st.selectbox("Timeframe", [
            "Intraday 5 Days (15m)",
            "Intraday 1 Day (5m)",
            "Swing 1 Month (1h)",
            "Trend 6 Months (1d)",
        ])
    with ch3:
        chart_style = st.radio("Style", ["Candlestick", "Area"], horizontal=True)
    with ch4:
        show_vwap = st.checkbox("VWAP & 20/50 EMA", value=True)
        show_rsi = st.checkbox("Volume & RSI (14)", value=True)

    tf_map = {
        "Intraday 5 Days (15m)": ("5d", "15m"),
        "Intraday 1 Day (5m)": ("1d", "5m"),
        "Swing 1 Month (1h)": ("1mo", "1h"),
        "Trend 6 Months (1d)": ("6mo", "1d"),
    }
    p_arg, i_arg = tf_map[tf_choice]
    hist_df = get_stock_chart_data(chart_sym, p_arg, i_arg)

    holding_row = None
    if not open_t.empty and chart_sym in open_t["symbol"].values:
        holding_row = open_t[open_t["symbol"] == chart_sym].iloc[0]

    if hist_df.empty:
        st.warning(f"No candle data available right now for {chart_sym}.")
    else:
        curr_px = float(hist_df["Close"].iloc[-1])
        prev_px = float(hist_df["Close"].iloc[0])
        chg_pct = ((curr_px - prev_px) / prev_px) * 100.0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{chart_sym} Live Price", f"₹{curr_px:,.2f}", f"{chg_pct:+.2f}%")
        if holding_row is not None:
            entry_v = float(holding_row["entry"])
            sl_v = float(holding_row["sl"])
            tgt_v = float(holding_row["target"])
            m2.metric("Entry Price", f"₹{entry_v:,.2f}", f"{holding_row['pnl_pct']:+.2f}%")
            m3.metric("Stop Loss (Red)", f"₹{sl_v:,.2f}", f"₹{holding_row['unrealized_pnl']:+,.2f}")
            m4.metric("Target Price (Green)", f"₹{tgt_v:,.2f}", f"Qty: {int(holding_row['qty'])}")

            # Progress Gauge toward Target
            prog_pct = max(0.0, min(100.0, ((curr_px - entry_v) / max(tgt_v - entry_v, 0.01)) * 100.0))
            st.progress(int(prog_pct), text=f"25%+10% Trailing Ladder Progress: {prog_pct:.1f}% toward Target (Breakeven lock triggers at 25%)")

        # Indicators
        hist_df["EMA20"] = hist_df["Close"].ewm(span=20).mean()
        hist_df["EMA50"] = hist_df["Close"].ewm(span=50).mean()
        typ = (hist_df["High"] + hist_df["Low"] + hist_df["Close"]) / 3.0
        hist_df["VWAP"] = (typ * hist_df["Volume"]).cumsum() / hist_df["Volume"].cumsum().replace(0, 1)

        delta = hist_df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        hist_df["RSI14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

        if show_rsi:
            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                row_heights=[0.62, 0.18, 0.20]
            )
        else:
            fig = make_subplots(rows=1, cols=1)

        if chart_style == "Candlestick":
            fig.add_trace(go.Candlestick(
                x=hist_df.index, open=hist_df["Open"], high=hist_df["High"], low=hist_df["Low"], close=hist_df["Close"],
                name=chart_sym, increasing_line_color="#10b981", decreasing_line_color="#ef4444"
            ), row=1, col=1)
        else:
            fig.add_trace(go.Scatter(
                x=hist_df.index, y=hist_df["Close"], mode="lines", fill="tozeroy",
                line=dict(color="#38bdf8", width=2), name=chart_sym
            ), row=1, col=1)

        if show_vwap:
            fig.add_trace(go.Scatter(x=hist_df.index, y=hist_df["EMA20"], mode="lines", name="EMA 20", line=dict(color="#f59e0b", width=1.3)), row=1, col=1)
            fig.add_trace(go.Scatter(x=hist_df.index, y=hist_df["EMA50"], mode="lines", name="EMA 50", line=dict(color="#a855f7", width=1.3)), row=1, col=1)
            if i_arg in ("5m", "15m"):
                fig.add_trace(go.Scatter(x=hist_df.index, y=hist_df["VWAP"], mode="lines", name="VWAP", line=dict(color="#06b6d4", width=1.5, dash="dot")), row=1, col=1)

        # Auto-frame Y-axis so SL, Entry, and Target are NEVER cut off
        y_min = float(hist_df["Low"].min())
        y_max = float(hist_df["High"].max())
        if holding_row is not None:
            e_px, s_px, t_px = float(holding_row["entry"]), float(holding_row["sl"]), float(holding_row["target"])
            y_min = min(y_min, s_px)
            y_max = max(y_max, t_px)
            fig.add_hline(y=e_px, line_dash="dash", line_color="#38bdf8", annotation_text=f"Entry ₹{e_px:,.2f}", row=1, col=1)
            fig.add_hline(y=s_px, line_dash="dot", line_color="#f87171", annotation_text=f"SL ₹{s_px:,.2f}", row=1, col=1)
            fig.add_hline(y=t_px, line_dash="dash", line_color="#34d399", annotation_text=f"Target ₹{t_px:,.2f}", row=1, col=1)

        fig.update_yaxes(range=[y_min * 0.995, y_max * 1.005], title_text="Price (₹)", row=1, col=1)

        if show_rsi:
            vol_colors = ["#10b981" if c >= o else "#ef4444" for c, o in zip(hist_df["Close"], hist_df["Open"])]
            fig.add_trace(go.Bar(x=hist_df.index, y=hist_df["Volume"], marker_color=vol_colors, name="Volume", showlegend=False), row=2, col=1)
            fig.update_yaxes(title_text="Vol", row=2, col=1)

            fig.add_trace(go.Scatter(x=hist_df.index, y=hist_df["RSI14"], mode="lines", name="RSI (14)", line=dict(color="#c084fc", width=1.6)), row=3, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", row=3, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="#10b981", row=3, col=1)
            fig.update_yaxes(range=[0, 100], title_text="RSI", row=3, col=1)

        # Strip weekend & overnight non-NSE gaps on intraday timeframes
        if i_arg in ("5m", "15m", "1h"):
            fig.update_xaxes(
                rangebreaks=[
                    dict(bounds=["sat", "mon"]),
                    dict(bounds=[15.5, 9.25], pattern="hour"),
                ]
            )
        else:
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b1324",
            plot_bgcolor="#081020",
            height=540 if show_rsi else 420,
            margin=dict(l=8, r=8, t=22, b=8),
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)

with tab_port:
    st.markdown("#### 💼 Active Virtual NSE Swing Holdings")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Available Cash", f"₹{cash:,.2f}")
    p2.metric("Holdings Value", f"₹{current_holdings_val:,.2f}")
    p3.metric("Unrealized P&L", f"₹{unrealized_pnl_total:+,.2f}")
    p4.metric("Total Swing Equity", f"₹{total_portfolio:,.2f}", f"{overall_ret_pct:+.2f}%")

    if open_t.empty:
        st.info("No open swing positions right now.")
    else:
        port_view = open_t[["timestamp", "symbol", "tier", "qty", "entry", "live_ltp", "sl", "target", "unrealized_pnl", "pnl_pct"]].copy()
        port_view.columns = ["Opened (IST)", "Stock", "Tier", "Qty", "Entry (₹)", "Live LTP (₹)", "Stop Loss (₹)", "Target (₹)", "Unrealized P&L (₹)", "Return %"]
        st.dataframe(port_view, use_container_width=True, hide_index=True)

        sell_c1, sell_c2 = st.columns([2, 1])
        with sell_c1:
            sym_to_close = st.selectbox("Select Open Position to Close Manually", open_t["symbol"].tolist())
        with sell_c2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💸 Close Position at Market LTP", use_container_width=True):
                trades_ws = sh.worksheet("trades")
                all_rows = trades_ws.get_all_records()
                for idx, row in enumerate(all_rows, start=2):
                    if row.get("symbol") == sym_to_close and row.get("status") == "OPEN":
                        exit_px = float(live_ltp_map.get(sym_to_close, row["entry"]))
                        qty_val = int(row["qty"])
                        entry_val = float(row["entry"])
                        pnl_val = round((exit_px - entry_val) * qty_val, 2)
                        new_cash = round(cash + exit_px * qty_val, 2)
                        man_id = now_ist().strftime("MAN-%H%M%S-IST")
                        trades_ws.update(f"D{idx}", [["MANUAL_EXIT"]])
                        trades_ws.update(f"I{idx}:J{idx}", [[exit_px, pnl_val]])
                        acct_ws.update("A2", [[new_cash]])
                        ag = infer_trade_agent(row)
                        new_scores = compute_agent_trust_scores(trades_ws.get_all_records())
                        record_trade_reflection(
                            sh, man_id, ag, sym_to_close, str(row.get("tier", "MID")),
                            "MANUAL_EXIT", pnl_val, entry_val, exit_px,
                            trust_scores.get(ag, {}).get("score", 50), new_scores.get(ag, {}).get("score", 50),
                        )
                        break
                st.cache_data.clear()
                st.rerun()

with tab_agents:
    st.markdown("#### 🏆 Agent Trust Leaderboard (0–100 ELO)")
    tc1, tc2, tc3 = st.columns(3)
    for col_obj, ag_key in zip([tc1, tc2, tc3], ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]):
        info = trust_scores[ag_key]
        with col_obj:
            st.metric(info["label"], f"Trust: {info['score']} / 100", f"{info['wins']}W - {info['losses']}L | ₹{info['net_pnl']:+,.2f}")

with tab_logs:
    # Fixed DeltaGenerator ternary glitch using clean if/else block
    l1, l2 = st.columns(2)
    with l1:
        st.markdown("#### 📜 Complete 30-Day Swing Trade Ledger (IST)")
        if not trades_df.empty:
            st.dataframe(trades_df, use_container_width=True, hide_index=True)
        else:
            st.write("No trades yet.")
    with l2:
        st.markdown("#### 💬 Inter-Agent Message Stream (IST)")
        for _, r in msgs_df.head(30).iterrows():
            with st.chat_message("assistant"):
                st.markdown(f"**`{r['timestamp']}`** — **{r['sender']}** ➡️ **{r['recipient']}**")
                st.write(r["summary"])

with tab_bt_live:
    st.markdown("#### 🧪 Historical Strategy Backtester (Zero Gemini Tokens)")
    bt1, bt2, bt3, bt4 = st.columns([1.1, 1.1, 1.1, 1.3])
    with bt1:
        bt_months = st.selectbox("Lookback Horizon", [3, 6, 9, 12], index=1, format_func=lambda m: f"{m} Months")
    with bt2:
        bt_univ = st.selectbox("Universe Size", [50, 100, 150, 250], index=1, format_func=lambda n: f"Top {n} NSE Stocks")
    with bt3:
        bt_slots = st.selectbox("Max Open Positions", [5, 10, 15, 20], index=2)
    with bt4:
        bt_floor = st.checkbox("Enable +₹7k Stepped Floor", value=True)
        bt_regime = st.checkbox("Enable NIFTY 20-SMA Filter", value=True)

    if st.button("🚀 Run Historical Backtest Simulation", type="primary"):
        with st.spinner(f"Replaying {bt_months} months across {bt_univ} NSE stocks..."):
            st.session_state["bt_result"] = run_historical_backtest(
                lookback_months=bt_months,
                initial_capital=1_000_000.0,
                max_open_positions=bt_slots,
                universe_size=bt_univ,
                use_nifty_regime_filter=bt_regime,
                use_profit_floor=bt_floor,
            )

    bt_res = st.session_state.get("bt_result")
    if bt_res and "metrics" in bt_res:
        m = bt_res["metrics"]
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Final Equity", f"₹{m['final_equity']:,.0f}", f"{m['net_return_pct']:+.2f}%")
        k2.metric("NIFTY 50 Return", f"{m['nifty_return_pct']:+.2f}%", f"Alpha: {m['alpha_pct']:+.2f}%")
        k3.metric("Win Rate", f"{m['win_rate_pct']}%", f"{m['total_closed_trades']} Closed")
        k4.metric("Profit Factor", f"{m['profit_factor']}", f"Avg Win ₹{m['avg_win']:,.0f}")
        k5.metric("Max Drawdown", f"{m['max_drawdown_pct']}%")
        k6.metric("Sharpe Ratio", f"{m['sharpe_ratio']}")

        eq_df = bt_res["equity_curve"]
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(x=eq_df["date"], y=eq_df["portfolio_equity"], mode="lines", name="V3.1 Swing Strategy", line=dict(color="#10b981", width=3)))
        fig_bt.add_trace(go.Scatter(x=eq_df["date"], y=eq_df["nifty_benchmark"], mode="lines", name="NIFTY 50 Buy & Hold", line=dict(color="#94a3b8", width=2, dash="dash")))
        fig_bt.update_layout(template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=320, margin=dict(l=10, r=10, t=15, b=10))
        st.plotly_chart(fig_bt, use_container_width=True)

    st.divider()
    st.markdown("#### 🔌 Live NSE Broker Bridge & SEBI 2026 Readiness")
    br1, br2, br3, br4 = st.columns([1.2, 1.2, 1.2, 1.4])
    with br1:
        broker_choice = st.selectbox("Zero-API Broker", ["DHAN (DhanHQ v2)", "ANGEL (SmartAPI)"])
    with br2:
        exec_mode = st.selectbox("Mode", ["SANDBOX (Paper Dry-Run)", "LIVE (Static-IP VPS Required)"])
    with br3:
        live_cap = st.number_input("Live Capital Allocation (₹)", min_value=10_000, max_value=5_000_000, value=100_000, step=10_000)
    with br4:
        daily_loss_cap = st.number_input("Hard Daily Loss Kill-Switch (₹)", min_value=1_000, max_value=250_000, value=5_000, step=500)

    adapter = get_broker_adapter("DHAN" if "DHAN" in broker_choice else "ANGEL", live_mode="LIVE" in exec_mode, allocated_capital=float(live_cap), max_daily_loss_inr=float(daily_loss_cap))
    if st.button("🧪 Test Broker Sandbox Order + GTT Bracket"):
        st.success(adapter.place_delivery_buy_with_gtt("RELIANCE.NS", 1, 1300.0, 1265.0, 1370.0).message)

if auto_refresh:
    import time
    time.sleep(60)
    st.cache_data.clear()
    st.rerun()
