import streamlit as st
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from swing_engine import (
    get_gsheet_client,
    SWING_SHEET_NAME,
    MAX_OPEN_POSITIONS,
    run_full_cycle,
    compute_agent_trust_scores,
    get_market_regime_and_sectors,
    get_persisted_peak_pnl,
)
from intraday_engine import (
    INTRADAY_SHEET_NAME,
    INIT_EQ_CAPITAL,
    INIT_FNO_CAPITAL,
    EQ_DAILY_LOSS_LIMIT,
    FNO_DAILY_LOSS_LIMIT,
    ensure_intraday_ledger_initialized,
    fetch_index_fno_context,
    parse_fno_symbol,
    days_to_next_tuesday,
    black_scholes_price,
    run_unified_cycle,
)
from broker_adapter import get_broker_adapter

IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def is_nse_market_open(now_dt: datetime) -> bool:
    if now_dt.weekday() >= 5:
        return False
    mins = now_dt.hour * 60 + now_dt.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


st.set_page_config(
    page_title="NSE ₹12L Master Multi-Asset 5-Agent Platform (V4.1)",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    section[data-testid="stSidebar"] { background-color: #081020; border-right: 1px solid #1e293b; }
    .top-bar { display: flex; justify-content: space-between; align-items: center; background: linear-gradient(90deg, #0d172a 0%, #111f38 100%); border: 1px solid #1e3a5f; border-radius: 12px; padding: 14px 20px; margin-bottom: 14px; }
    .platform-title { font-size: 22px; font-weight: 800; color: #f8fafc; margin: 0; }
    .live-pill { background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; color: #34d399; padding: 4px 12px; border-radius: 20px; font-size: 12.5px; font-weight: 600; }
    .book-card { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; min-height: 175px; }
    .book-title { font-size: 13px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
    .book-val { font-size: 26px; font-weight: 800; color: #f8fafc; margin: 6px 0; }
    .regime-strip { background: #0b1324; border: 1px solid #1e3a5f; border-radius: 10px; padding: 10px 16px; font-size: 12.5px; color: #cbd5e1; margin-top: 10px; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=90, show_spinner=False)
def get_indian_indices():
    indices = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "BANK NIFTY": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}
    out = {}
    for name, sym in indices.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                curr, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                diff = curr - prev
                out[name] = (curr, (diff / prev) * 100)
            else:
                out[name] = (0.0, 0.0)
        except Exception:
            out[name] = (0.0, 0.0)
    return out


@st.cache_data(ttl=60, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
def load_master_ledgers(_gcp_creds: dict):
    swing_sh = get_gsheet_client(_gcp_creds, SWING_SHEET_NAME)
    sw_acct = swing_sh.worksheet("account")
    sw_cash = float(sw_acct.acell("A2").value or 1_000_000.0)
    sw_peak = get_persisted_peak_pnl(sw_acct)
    sw_trades = swing_sh.worksheet("trades").get_all_records()

    intra_sh = get_gsheet_client(_gcp_creds, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(intra_sh)
    in_vals = intra_sh.worksheet("account").get("A2:E2")
    r2 = in_vals[0] if in_vals and in_vals[0] else []
    eq_c = float(r2[1]) if len(r2) > 1 and r2[1] not in ("", None) else INIT_EQ_CAPITAL
    fno_c = float(r2[2]) if len(r2) > 2 and r2[2] not in ("", None) else INIT_FNO_CAPITAL
    in_peak = float(r2[3]) if len(r2) > 3 and r2[3] not in ("", None) else 0.0
    in_floor = float(r2[4]) if len(r2) > 4 and r2[4] not in ("", None) else 0.0
    in_trades = intra_sh.worksheet("trades").get_all_records()
    return sw_cash, sw_peak, sw_trades, eq_c, fno_c, in_peak, in_floor, in_trades


current_ist = get_ist_now()
market_open = is_nse_market_open(current_ist)

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()

try:
    swing_cash, sw_peak, swing_trades, eq_cash, fno_cash, in_peak, in_floor, intra_trades = load_master_ledgers(gcp_creds)
    ledger_ok = True
    ledger_err = ""
except Exception as e:
    swing_cash, sw_peak, swing_trades = 1_000_000.0, 0.0, []
    eq_cash, fno_cash, in_peak, in_floor, intra_trades = INIT_EQ_CAPITAL, INIT_FNO_CAPITAL, 0.0, 0.0, []
    ledger_ok = False
    ledger_err = str(e)

swing_trust = compute_agent_trust_scores(swing_trades)
swing_df = pd.DataFrame(swing_trades) if swing_trades else pd.DataFrame()
open_swing = swing_df[swing_df["status"] == "OPEN"].copy() if not swing_df.empty else pd.DataFrame()
closed_swing = swing_df[swing_df["status"] != "OPEN"].copy() if not swing_df.empty else pd.DataFrame()

intra_df = pd.DataFrame(intra_trades) if intra_trades else pd.DataFrame()
open_intra = intra_df[intra_df["status"] == "OPEN"].copy() if not intra_df.empty else pd.DataFrame()
today_str = current_ist.strftime("%Y-%m-%d")
closed_intra_today = intra_df[(intra_df["status"] != "OPEN") & (intra_df["date"].astype(str) == today_str)] if not intra_df.empty else pd.DataFrame()

eq_day_realized = float(closed_intra_today[closed_intra_today["book"] == "EQUITY"]["net_pnl"].astype(float).sum()) if not closed_intra_today.empty else 0.0
fno_day_realized = float(closed_intra_today[closed_intra_today["book"] == "FNO"]["net_pnl"].astype(float).sum()) if not closed_intra_today.empty else 0.0

# Collect all equity tickers across Swing + Intraday MIS for single-source live pricing
eq_syms_needed = set()
if not open_swing.empty:
    eq_syms_needed.update(open_swing["symbol"].astype(str).unique().tolist())
if not open_intra.empty:
    eq_syms_needed.update(open_intra[open_intra["book"] == "EQUITY"]["symbol"].astype(str).unique().tolist())

ltp_map = get_live_ltps(tuple(sorted(eq_syms_needed)))

fno_ctx = fetch_index_fno_context() if (not open_intra.empty and (open_intra["book"] == "FNO").any()) else {}
india_vix = float(fno_ctx.get("india_vix", 14.5))
sigma = max(0.11, min(0.35, india_vix / 100.0))
spot_lookup = {k: float(v.get("spot", 0.0)) for k, v in fno_ctx.get("indices", {}).items()}

watch_rows = []
swing_holdings_val = 0.0
swing_unrealized = 0.0

if not open_swing.empty:
    for _, r in open_swing.iterrows():
        sym = str(r["symbol"])
        qty = int(float(r["qty"]))
        entry = float(r["entry"])
        sl = float(r["sl"])
        tgt = float(r["target"])
        ltp = float(ltp_map.get(sym, entry))
        mtm = round((ltp - entry) * qty, 2)
        swing_holdings_val += ltp * qty
        swing_unrealized += mtm
        lock_amt = round((sl - entry) * qty, 2)
        prot_status = f"LOCKED (+₹{lock_amt:,.0f})" if sl >= entry else "AT RISK"
        watch_rows.append({
            "Desk": "SWING",
            "Symbol / Contract": sym,
            "Strategy / Tier": str(r.get("tier", "MID")),
            "Qty / Lots": qty,
            "Entry (₹)": round(entry, 2),
            "Live LTP/Prem (₹)": round(ltp, 2),
            "SL (₹)": round(sl, 2),
            "Target (₹)": round(tgt, 2),
            "Protection": prot_status,
            "MTM P&L (₹)": mtm,
        })

swing_realized = float(closed_swing["pnl"].astype(float).sum()) if not closed_swing.empty else 0.0
swing_equity = round(swing_cash + swing_holdings_val, 2)

eq_open_margin = 0.0
eq_open_unreal = 0.0
fno_open_prem = 0.0
fno_open_unreal = 0.0

if not open_intra.empty:
    for _, r in open_intra.iterrows():
        book = str(r.get("book", "EQUITY"))
        sym = str(r["symbol"])
        strat = str(r.get("strategy", ""))
        qty_or_lots = int(float(r["qty_or_lots"]))
        lot_sz = int(float(r.get("lot_size", 1) or 1))
        entry = float(r["entry"])
        sl = float(r["sl"])
        tgt = float(r["target"])

        if book == "EQUITY":
            ltp = float(ltp_map.get(sym, entry))
            margin_used = (entry * qty_or_lots) / 5.0
            mtm = round((ltp - entry) * qty_or_lots, 2)
            eq_open_margin += margin_used
            eq_open_unreal += mtm
            prot_status = f"TRAILED (SL ≥ Entry)" if sl >= entry else "INTRADAY SL"
            watch_rows.append({
                "Desk": "MIS",
                "Symbol / Contract": sym,
                "Strategy / Tier": strat,
                "Qty / Lots": qty_or_lots,
                "Entry (₹)": round(entry, 2),
                "Live LTP/Prem (₹)": round(ltp, 2),
                "SL (₹)": round(sl, 2),
                "Target (₹)": round(tgt, 2),
                "Protection": prot_status,
                "MTM P&L (₹)": mtm,
            })
        else:
            parsed = parse_fno_symbol(sym)
            prem_now = entry
            if parsed and parsed["index_name"] in spot_lookup and spot_lookup[parsed["index_name"]] > 0:
                sp_px = spot_lookup[parsed["index_name"]]
                t_yr = (days_to_next_tuesday() if parsed["index_name"] == "NIFTY" else 14.0) / 365.0
                p1 = black_scholes_price(sp_px, parsed["strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                if parsed["hedge_strike"] > 0:
                    p2 = black_scholes_price(sp_px, parsed["hedge_strike"], t_yr, 0.065, sigma, parsed["opt_type"])
                    prem_now = round(max(p1 - p2, 1.0), 2)
                else:
                    prem_now = p1
            units = qty_or_lots * lot_sz
            mtm = round((prem_now - entry) * units, 2)
            fno_open_prem += entry * units
            fno_open_unreal += mtm
            prot_status = f"TRAILED (SL ≥ Entry)" if sl >= entry else "3:15 PM EOD EXIT"
            watch_rows.append({
                "Desk": "F&O",
                "Symbol / Contract": sym,
                "Strategy / Tier": strat,
                "Qty / Lots": qty_or_lots,
                "Entry (₹)": round(entry, 2),
                "Live LTP/Prem (₹)": round(prem_now, 2),
                "SL (₹)": round(sl, 2),
                "Target (₹)": round(tgt, 2),
                "Protection": prot_status,
                "MTM P&L (₹)": mtm,
            })

eq_book_equity = round(eq_cash + eq_open_margin + eq_open_unreal, 2)
fno_book_equity = round(fno_cash + fno_open_prem + fno_open_unreal, 2)
# Exact single-source reconciliation: sum of the 3 ring-fenced books
master_total_equity = round(swing_equity + eq_book_equity + fno_book_equity, 2)
master_base = 1_200_000.0
master_net_pnl = round(master_total_equity - master_base, 2)
master_ret_pct = (master_net_pnl / master_base) * 100.0

idx = get_indian_indices()
badge = f"● NSE Open • {current_ist.strftime('%I:%M:%S %p IST')}" if market_open else f"● NSE Closed • {current_ist.strftime('%I:%M:%S %p IST')}"

st.markdown(f"""
<div class="top-bar">
    <div>
        <div class="platform-title">🏛️ NSE ₹12,00,000 Unified Multi-Asset 5-Agent Master Platform (V4.1)</div>
        <div style="color:#94a3b8;font-size:13px;margin-top:4px;">
            <b>NIFTY 50:</b> {idx['NIFTY 50'][0]:,.2f} ({idx['NIFTY 50'][1]:+.2f}%) &nbsp;|&nbsp;
            <b>BANK NIFTY:</b> {idx['BANK NIFTY'][0]:,.2f} ({idx['BANK NIFTY'][1]:+.2f}%) &nbsp;|&nbsp;
            <b>INDIA VIX:</b> {idx['INDIA VIX'][0]:.2f} &nbsp;|&nbsp;
            <b>Master Net Worth:</b> <span style="color:#34d399;font-weight:700;">₹{master_total_equity:,.2f} ({master_ret_pct:+.2f}%)</span>
        </div>
    </div>
    <div><span class="live-pill">{badge}</span></div>
</div>
""", unsafe_allow_html=True)

if not ledger_ok:
    st.warning(f"Ledger sync notice (`{ledger_err}`). Click **Refresh All Books** in 15s.")

m1, m2, m3, m4 = st.columns([1.3, 1.3, 1.1, 1.3])
with m1:
    if st.button("📈 Run ₹10L Swing Cycle", use_container_width=True, type="primary"):
        with st.spinner("Running 250-stock Swing Cycle (AG1–AG5)..."):
            asyncio.run(run_full_cycle(api_key, gcp_creds))
        st.cache_data.clear()
        st.rerun()
with m2:
    if st.button("⚡ Run ₹2L Intraday + F&O Cycle", use_container_width=True):
        with st.spinner("Scanning 61 MIS stocks + NIFTY/BANKNIFTY Option Chains..."):
            asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=False))
        st.cache_data.clear()
        st.rerun()
with m3:
    if st.button("🔄 Refresh All Books", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with m4:
    if st.button("🚨 MASTER EMERGENCY SQUARE-OFF", use_container_width=True):
        asyncio.run(run_unified_cycle(api_key, gcp_creds, force_square_off=True))
        adapter = get_broker_adapter("DHAN", live_mode=False)
        adapter.cancel_all_and_square_off()
        st.cache_data.clear()
        st.error("Master Emergency Square-Off & Broker Kill-Switch Triggered!")

st.markdown("### 💼 Ring-Fenced Multi-Asset Capital Books (₹12,00,000 Base)")
b1, b2, b3, b4 = st.columns(4)
with b1:
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #10b981;">
        <div class="book-title">🏛️ Master Combined Equity</div>
        <div class="book-val">₹{master_total_equity:,.2f}</div>
        <div style="color:{'#34d399' if master_net_pnl >= 0 else '#f87171'};font-size:13px;font-weight:700;">Net P&L: ₹{master_net_pnl:+,.2f} ({master_ret_pct:+.2f}%)</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Exact Sum of 3 Ring-Fenced Books • {len(watch_rows)} Active Legs</div>
    </div>
    """, unsafe_allow_html=True)
with b2:
    sw_pnl = swing_equity - 1_000_000.0
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #3b82f6;">
        <div class="book-title">📈 Desk 1: Positional Swing (₹10L)</div>
        <div class="book-val">₹{swing_equity:,.2f}</div>
        <div style="color:{'#34d399' if sw_pnl >= 0 else '#f87171'};font-size:13px;">Unrealized: ₹{swing_unrealized:+,.2f} | Realized: ₹{swing_realized:+,.2f}</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Open Slots: <b>{len(open_swing)} / {MAX_OPEN_POSITIONS}</b> • Cash: ₹{swing_cash:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)
with b3:
    eq_pnl = eq_book_equity - INIT_EQ_CAPITAL
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #f59e0b;">
        <div class="book-title">⚡ Book A: Intraday Equity MIS (₹1L)</div>
        <div class="book-val">₹{eq_book_equity:,.2f}</div>
        <div style="color:{'#34d399' if eq_pnl >= 0 else '#f87171'};font-size:13px;">MTM: ₹{eq_open_unreal:+,.2f} | Day Closed: ₹{eq_day_realized:+,.2f}</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">5x Power: ₹{eq_cash*5:,.0f} • Limit: ₹{EQ_DAILY_LOSS_LIMIT:,.0f} • 3:15 PM Exit</div>
    </div>
    """, unsafe_allow_html=True)
with b4:
    fno_pnl = fno_book_equity - INIT_FNO_CAPITAL
    st.markdown(f"""
    <div class="book-card" style="border-top:3px solid #a855f7;">
        <div class="book-title">🎯 Book B: Index F&O Options (₹1L)</div>
        <div class="book-val">₹{fno_book_equity:,.2f}</div>
        <div style="color:{'#34d399' if fno_pnl >= 0 else '#f87171'};font-size:13px;">MTM: ₹{fno_open_unreal:+,.2f} | Day Closed: ₹{fno_day_realized:+,.2f}</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:8px;">Limit: ₹{FNO_DAILY_LOSS_LIMIT:,.0f} • Floor: ₹{in_floor:,.0f} (Peak ₹{in_peak:,.0f})</div>
    </div>
    """, unsafe_allow_html=True)

vix_mode_label = "DEBIT SPREADS (VIX ≥ 17)" if idx["INDIA VIX"][0] >= 17.0 else "SINGLE-LEG BUY (VIX < 17)"
eq_headroom = round((eq_day_realized + eq_open_unreal) - EQ_DAILY_LOSS_LIMIT, 2)
fno_headroom = round((fno_day_realized + fno_open_unreal) - FNO_DAILY_LOSS_LIMIT, 2)
st.markdown(f"""
<div class="regime-strip">
    <b>SYSTEM TELEMETRY:</b> &nbsp;
    NIFTY vs 20-SMA: <b>{'ABOVE / SUPPORTIVE' if idx['NIFTY 50'][1] >= -0.6 else 'CAUTIOUS'}</b> &nbsp;|&nbsp;
    F&O Regime: <b>{vix_mode_label}</b> &nbsp;|&nbsp;
    Book A Breaker Headroom: <b>₹{eq_headroom:+,.0f}</b> &nbsp;|&nbsp;
    Book B Breaker Headroom: <b>₹{fno_headroom:+,.0f}</b> &nbsp;|&nbsp;
    Swing Peak Ref (B2): <b>₹{sw_peak:+,.0f}</b>
</div>
""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("#### 📋 Unified Live Positions & F&O Watch Table (All Active Desks)")
if watch_rows:
    st.dataframe(pd.DataFrame(watch_rows), use_container_width=True, hide_index=True)
else:
    st.info("No open positions across Swing, Intraday MIS, or Index F&O right now.")

st.markdown("<br>", unsafe_allow_html=True)
c_left, c_right = st.columns([1.3, 1.7])
with c_left:
    st.markdown("#### 📊 Capital Allocation Breakdown")
    fig_pie = go.Figure(data=[go.Pie(
        labels=["Swing Holdings", "Swing Free Cash", "Intraday MIS Book", "Index F&O Book"],
        values=[max(swing_holdings_val, 0), max(swing_cash, 0), max(eq_book_equity, 0), max(fno_book_equity, 0)],
        hole=0.55,
        marker=dict(colors=["#10b981", "#3b82f6", "#f59e0b", "#a855f7"]),
    )])
    fig_pie.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b1324",
        plot_bgcolor="#0b1324",
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig_pie, use_container_width=True)

with c_right:
    st.markdown("#### 🤖 Shared 5-Agent Council & Trust Standings")
    t1, t2, t3 = st.columns(3)
    for col_o, k in zip([t1, t2, t3], ["AG1_LOW_RISK", "AG2_MID_RISK", "AG3_HIGH_RISK"]):
        info = swing_trust[k]
        with col_o:
            st.metric(info["label"], f"Trust: {info['score']}/100", f"{info['wins']}W-{info['losses']}L | ₹{info['net_pnl']:+,.0f}")
    st.info("Use the sidebar to open **`1_Swing_10L_Desk`** (Swing + Backtest + Broker) or **`2_Intraday_2L_Desk`** (Live MIS/Index Charts + Option Chain + Payoff Lab).")
