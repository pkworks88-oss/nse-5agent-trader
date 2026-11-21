import streamlit as st
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from swing_engine import get_gsheet_client, SWING_SHEET_NAME
from intraday_engine import (
    INTRADAY_SHEET_NAME,
    INIT_EQ_CAPITAL,
    INIT_FNO_CAPITAL,
    EQ_MIS_LEVERAGE,
    ensure_intraday_ledger_initialized,
    fetch_index_fno_context,
    parse_fno_symbol,
    days_to_next_tuesday,
    black_scholes_price,
    compute_book_and_combined_trust,
)
from swing_desk_helpers import (
    INIT_SWING_CAPITAL,
    MAX_SWING_SLOTS,
    read_swing_account_state,
    compute_swing_open_downside_risk,
    render_circular_trust_badge,
)

IST = timezone(timedelta(hours=5, minutes=30))

st.markdown("""
<style>
    .stApp { background: radial-gradient(circle at top right, #0f1c36 0%, #070d19 60%, #050912 100%); color: #e2e8f0; }
    .panel-box { background: #0b1324; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
</style>
""", unsafe_allow_html=True)


def tier_badge_html(tier_str: str) -> str:
    t_up = str(tier_str).upper()
    if "LOW" in t_up:
        bg, fg, br = "rgba(16, 185, 129, 0.16)", "#34d399", "rgba(16, 185, 129, 0.35)"
    elif "HIGH" in t_up:
        bg, fg, br = "rgba(239, 68, 68, 0.18)", "#f87171", "rgba(239, 68, 68, 0.38)"
    else:
        bg, fg, br = "rgba(245, 158, 11, 0.16)", "#fbbf24", "rgba(245, 158, 11, 0.35)"
    return f"<span style='background:{bg};color:{fg};border:1px solid {br};padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'>{t_up}</span>"


def protection_badge_html(entry: float, sl: float, units: int) -> str:
    if sl > entry:
        locked = round((sl - entry) * units, 0)
        return f"<span style='background:rgba(16, 185, 129, 0.18);color:#34d399;border:1px solid rgba(16, 185, 129, 0.4);padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>Locked +₹{locked:,.0f}</span>"
    return "<span style='background:rgba(239, 68, 68, 0.18);color:#f87171;border:1px solid rgba(239, 68, 68, 0.4);padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;'>At risk</span>"


if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
    raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    gcp_creds = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
elif "gcp_service_account" in st.secrets:
    gcp_creds = dict(st.secrets["gcp_service_account"])
else:
    st.error("Missing `GCP_SERVICE_ACCOUNT_JSON` in Streamlit Secrets.")
    st.stop()


@st.cache_data(ttl=90, show_spinner=False)
def load_master_books(_gcp_creds: dict):
    sh_sw = get_gsheet_client(_gcp_creds, SWING_SHEET_NAME)
    sw_trades = sh_sw.worksheet("trades").get_all_records()
    sw_acct = read_swing_account_state(sh_sw, default_cash=INIT_SWING_CAPITAL)

    sh_in = get_gsheet_client(_gcp_creds, INTRADAY_SHEET_NAME)
    ensure_intraday_ledger_initialized(sh_in)
    acct_r2 = sh_in.worksheet("account").get("A2:E2")[0]
    eq_c = float(acct_r2[1] or INIT_EQ_CAPITAL)
    fno_c = float(acct_r2[2] or INIT_FNO_CAPITAL)
    in_trades = sh_in.worksheet("trades").get_all_records()
    return sw_acct["cash"], sw_trades, eq_c, fno_c, in_trades


@st.cache_data(ttl=90, show_spinner=False)
def fetch_ltps_batch(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    out = {}
    try:
        raw = yf.download(symbols, period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        for s in symbols:
            sub = raw[s]["Close"].dropna() if len(symbols) > 1 else raw["Close"].dropna()
            if not sub.empty:
                out[s] = round(float(sub.iloc[-1]), 2)
    except Exception:
        pass
    return out


@st.cache_data(ttl=120, show_spinner=False)
def fetch_nifty_master_bench(period: str = "10d") -> pd.Series:
    try:
        df = yf.Ticker("^NSEI").history(period=period, interval="1d")
        if df is not None and not df.empty:
            s = df["Close"].dropna()
            return (s / float(s.iloc[0])) * 1_200_000.0
    except Exception:
        pass
    return pd.Series(dtype=float)


now_ist_dt = datetime.now(timezone.utc).astimezone(IST)
sw_cash, sw_trades, eq_cash, fno_cash, in_trades = load_master_books(gcp_creds)
fno_ctx = fetch_index_fno_context()
india_vix = float(fno_ctx.get("india_vix", 14.5))
sigma = max(0.11, min(0.35, india_vix / 100.0))
nifty_spot = float(fno_ctx.get("indices", {}).get("NIFTY", {}).get("spot", 25500.0))
bnifty_spot = float(fno_ctx.get("indices", {}).get("BANKNIFTY", {}).get("spot", 63300.0))

sw_df = pd.DataFrame(sw_trades) if sw_trades else pd.DataFrame()
in_df = pd.DataFrame(in_trades) if in_trades else pd.DataFrame()

sw_open = sw_df[sw_df["status"] == "OPEN"].copy() if not sw_df.empty else pd.DataFrame()
in_open = in_df[in_df["status"] == "OPEN"].copy() if not in_df.empty else pd.DataFrame()

all_eq_syms = list({
    *(sw_open["symbol"].astype(str).tolist() if not sw_open.empty else []),
    *(in_open[in_open["book"] == "EQUITY"]["symbol"].astype(str).tolist() if not in_open.empty else []),
})
ltp_map = fetch_ltps_batch(all_eq_syms)

sw_holdings_val = 0.0
watch_rows = []

for _, r in sw_open.iterrows():
    sym = str(r["symbol"])
    qty = int(float(r["qty"]))
    ent = float(r["entry"])
    sl = float(r["sl"])
    tp = float(r["target"])
    ltp = float(ltp_map.get(sym, ent))
    mtm = round((ltp - ent) * qty, 2)
    prog = round(((ltp - ent) / max(tp - ent, 0.5)) * 100.0, 1)
    sw_holdings_val += ltp * qty
    watch_rows.append({
        "symbol": sym.replace(".NS", ""), "desk": "Swing", "tier": str(r.get("tier", "MID")),
        "qty": qty, "units": qty, "entry": ent, "ltp": ltp, "sl": sl, "target": tp, "prog": prog, "mtm": mtm
    })

mis_unreal = 0.0
mis_margin = 0.0
fno_unreal = 0.0
fno_prem = 0.0

for _, r in in_open.iterrows():
    bk = str(r.get("book", "EQUITY"))
    sym = str(r["symbol"])
    q = int(float(r["qty_or_lots"]))
    ls = int(float(r.get("lot_size", 1) or 1))
    ent = float(r["entry"])
    sl = float(r["sl"])
    tp = float(r["target"])
    if bk == "EQUITY":
        ltp = float(ltp_map.get(sym, ent))
        mtm = round((ltp - ent) * q, 2)
        prog = round(((ltp - ent) / max(tp - ent, 0.5)) * 100.0, 1)
        mis_unreal += mtm
        mis_margin += (ent * q) / EQ_MIS_LEVERAGE
        watch_rows.append({
            "symbol": sym.replace(".NS", ""), "desk": "Intraday MIS", "tier": str(r.get("strategy", "MIS_MID")).replace("MIS_", ""),
            "qty": q, "units": q, "entry": ent, "ltp": ltp, "sl": sl, "target": tp, "prog": prog, "mtm": mtm
        })
    else:
        parsed = parse_fno_symbol(sym)
        now_p = ent
        if parsed:
            sp_n = nifty_spot if parsed["index_name"] == "NIFTY" else bnifty_spot
            t_y = (days_to_next_tuesday() if parsed["index_name"] == "NIFTY" else 14.0) / 365.0
            p1 = black_scholes_price(sp_n, parsed["strike"], t_y, 0.065, sigma, parsed["opt_type"])
            now_p = round(max(p1 - black_scholes_price(sp_n, parsed["hedge_strike"], t_y, 0.065, sigma, parsed["opt_type"]), 1.0), 2) if parsed["hedge_strike"] > 0 else p1
        mtm = round((now_p - ent) * q * ls, 2)
        prog = round(((now_p - ent) / max(tp - ent, 0.5)) * 100.0, 1)
        fno_unreal += mtm
        fno_prem += ent * q * ls
        watch_rows.append({
            "symbol": sym, "desk": "Index F&O", "tier": "MID",
            "qty": q, "units": q * ls, "entry": ent, "ltp": now_p, "sl": sl, "target": tp, "prog": prog, "mtm": mtm
        })

sw_total_eq = round(sw_cash + sw_holdings_val, 2)
mis_total_eq = round(eq_cash + mis_margin + mis_unreal, 2)
fno_total_eq = round(fno_cash + fno_prem + fno_unreal, 2)
master_eq = round(sw_total_eq + mis_total_eq + fno_total_eq, 2)
master_pnl = round(master_eq - 1_200_000.0, 2)

hd1, hd2 = st.columns([3.4, 1.0])
with hd1:
    st.markdown("## 📊 Dashboard — ₹12,00,000 Master Multi-Asset Overview (V4.7)")
    st.caption(f"NIFTY 50: **{nifty_spot:,.2f}** • BANK NIFTY: **{bnifty_spot:,.2f}** • India VIX: **{india_vix:.2f}** • IST: **{now_ist_dt.strftime('%I:%M:%S %p')}**")
with hd2:
    st.markdown("<div style='padding-top:12px;'></div>", unsafe_allow_html=True)
    if st.button("🔄 Refresh All Books", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Master Combined Equity", f"₹{master_eq:,.2f}", f"₹{master_pnl:+,.2f} ({(master_pnl / 1200000.0) * 100:+.2f}%)")
c2.metric("Desk 1: Positional Swing", f"₹{sw_total_eq:,.2f}", f"Slots {len(sw_open)}/{MAX_SWING_SLOTS} | Cash ₹{sw_cash:,.0f}")
c3.metric("Book A: Intraday MIS", f"₹{mis_total_eq:,.2f}", f"Free Cash ₹{eq_cash:,.2f}")
c4.metric("Book B: Index F&O", f"₹{fno_total_eq:,.2f}", f"Free Cash ₹{fno_cash:,.2f}")

# Master Equity vs NIFTY 50 Benchmark + Capital Allocation Donut
ch1, ch2 = st.columns([1.8, 1.2])
with ch1:
    rebased_pct = st.toggle("Show Rebased % Return", value=False, key="dash_rebased_pct")
    bench_m = fetch_nifty_master_bench("10d")
    fig_m = go.Figure()
    if not bench_m.empty:
        y_b = ((bench_m.values / 1_200_000.0) - 1.0) * 100.0 if rebased_pct else bench_m.values
        fig_m.add_trace(go.Scatter(x=bench_m.index, y=y_b, mode="lines", name="NIFTY 50 Benchmark", line=dict(color="#94a3b8", width=2, dash="dash")))
    y_p = [0.0, (master_pnl / 1_200_000.0) * 100.0] if rebased_pct else [1_200_000.0, master_eq]
    fig_m.add_trace(go.Scatter(x=[now_ist_dt - timedelta(days=2), now_ist_dt], y=y_p, mode="lines+markers", name="Master Equity", line=dict(color="#10b981", width=3)))
    fig_m.update_layout(
        template="plotly_dark", paper_bgcolor="#0b1324", plot_bgcolor="#081020", height=250,
        title="Master Equity vs. NIFTY 50", yaxis_title="Return (%)" if rebased_pct else "Equity (₹)",
        margin=dict(l=8, r=8, t=34, b=8),
    )
    st.plotly_chart(fig_m, use_container_width=True)
with ch2:
    alloc_labels = ["Swing Holdings", "Swing Cash", "Intraday MIS", "Index F&O"]
    alloc_vals = [max(0.0, sw_holdings_val), max(0.0, sw_cash), max(0.0, mis_total_eq), max(0.0, fno_total_eq)]
    fig_al = go.Figure(go.Pie(
        labels=alloc_labels, values=alloc_vals, hole=0.55,
        marker=dict(colors=["#10b981", "#38bdf8", "#6366f1", "#f59e0b"]),
    ))
    fig_al.update_layout(
        template="plotly_dark", paper_bgcolor="#0b1324", height=285,
        title="Capital Allocation Breakdown", margin=dict(l=8, r=8, t=34, b=8),
        legend=dict(orientation="h", y=-0.12),
    )
    st.plotly_chart(fig_al, use_container_width=True)

# Trust Score Matrix across all books (50-point baseline + Circular SVG Rings)
trust_all = compute_book_and_combined_trust(in_trades, sw_trades)
sw_downside_risk = compute_swing_open_downside_risk(sw_open)

st.markdown("#### 🧭 Shared 5-Agent Council & Circular Trust Standings (Baseline: 50/100)")
t_cols = st.columns(4)
ag_meta = [
    ("AG1_LOW_RISK", "Low Risk"),
    ("AG2_MID_RISK", "Mid Risk"),
    ("AG3_HIGH_RISK", "High Risk"),
]
for idx_a, (ag_id, ag_sub) in enumerate(ag_meta):
    with t_cols[idx_a]:
        st.markdown(
            render_circular_trust_badge(
                ag_id,
                ag_sub,
                trust_all["COMBINED"][ag_id],
                f"Swing <b>{trust_all['SWING'][ag_id]}</b> • MIS <b>{trust_all['EQUITY_MIS'][ag_id]}</b> • F&O <b>{trust_all['FNO'][ag_id]}</b>",
            ),
            unsafe_allow_html=True,
        )
with t_cols[3]:
    st.markdown(
        f"<div class='panel-box'>"
        f"<div style='font-size:12px;color:#94a3b8;font-weight:700;'>RISK & SLOT HEALTH</div>"
        f"<div style='font-size:20px;font-weight:800;color:#fbbf24;'>₹{sw_downside_risk:,.0f} <span style='font-size:12px;color:#94a3b8;'>Open Stop Risk</span></div>"
        f"<div style='font-size:12px;color:#cbd5e1;margin-top:4px;'>Gross Deployed: <b>₹{sw_holdings_val:,.0f}</b></div>"
        f"<div style='font-size:12px;color:#cbd5e1;'>Swing Slots: <b>{len(sw_open)}/{MAX_SWING_SLOTS}</b> {'⚠️ Over Cap' if len(sw_open) > MAX_SWING_SLOTS else '✅ Within Cap'}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

# Colorful Unified Watch Table with Protection Filter & Target Progress %
st.markdown("#### 🎯 Unified Live Positions & F&O Watch Table")
prot_filter = st.radio("Filter Watch Table", ["All Positions", "Locked Profit Only", "At Risk Only"], horizontal=True, key="dash_prot_filter")
filtered_watch = watch_rows
if prot_filter == "Locked Profit Only":
    filtered_watch = [w for w in watch_rows if w["sl"] > w["entry"]]
elif prot_filter == "At Risk Only":
    filtered_watch = [w for w in watch_rows if w["sl"] <= w["entry"]]

if not filtered_watch:
    st.info("No positions match the selected filter.")
else:
    html_tbl = [
        "<div class='panel-box' style='padding:14px;'><div style='overflow-x:auto;'>",
        "<table style='width:100%;border-collapse:collapse;font-size:13px;'>",
        "<thead><tr style='border-bottom:1px solid #1e293b;color:#94a3b8;font-size:11px;text-transform:uppercase;'>",
        "<th style='text-align:left;padding:8px;'>Symbol</th><th style='text-align:left;padding:8px;'>Desk</th><th style='text-align:left;padding:8px;'>Tier</th>",
        "<th style='text-align:right;padding:8px;'>Qty</th><th style='text-align:right;padding:8px;'>Entry ₹</th><th style='text-align:right;padding:8px;'>LTP ₹</th>",
        "<th style='text-align:right;padding:8px;'>Stop ₹</th><th style='text-align:right;padding:8px;'>Target ₹</th><th style='text-align:right;padding:8px;'>Prog %</th>",
        "<th style='text-align:center;padding:8px;'>Protection</th><th style='text-align:right;padding:8px;'>MTM P&L ₹</th></tr></thead><tbody>",
    ]
    for w in filtered_watch:
        m_col = "#34d399" if w["mtm"] >= 0 else "#f87171"
        m_str = f"+{w['mtm']:,.2f}" if w["mtm"] > 0 else f"{w['mtm']:,.2f}"
        p_col = "#34d399" if w["prog"] >= 20 else ("#f87171" if w["prog"] < 0 else "#cbd5e1")
        html_tbl.append(
            f"<tr style='border-bottom:1px solid rgba(30,41,59,0.65);'>"
            f"<td style='padding:9px;font-weight:700;color:#f8fafc;'>{w['symbol']}</td>"
            f"<td style='padding:9px;color:#cbd5e1;'>{w['desk']}</td>"
            f"<td style='padding:9px;'>{tier_badge_html(w['tier'])}</td>"
            f"<td style='padding:9px;text-align:right;'>{w['qty']:,}</td>"
            f"<td style='padding:9px;text-align:right;'>{w['entry']:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;font-weight:600;'>{w['ltp']:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;'>{w['sl']:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;'>{w['target']:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;font-weight:600;color:{p_col};'>{w['prog']:+.1f}%</td>"
            f"<td style='padding:9px;text-align:center;'>{protection_badge_html(w['entry'], w['sl'], w['units'])}</td>"
            f"<td style='padding:9px;text-align:right;font-weight:700;color:{m_col};'>{m_str}</td></tr>"
        )
    html_tbl.append("</tbody></table></div></div>")
    st.markdown("".join(html_tbl), unsafe_allow_html=True)
