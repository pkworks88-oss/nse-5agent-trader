import streamlit as st
import os
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
from swing_engine import get_gsheet_client, SWING_SHEET_NAME
from intraday_engine import compute_book_and_combined_trust
from swing_desk_helpers import compute_swing_open_downside_risk, MAX_SWING_SLOTS, MAX_SWING_PER_SECTOR
from skills.market_skills import get_sector, get_sector_counts

IST = timezone(timedelta(hours=5, minutes=30))
INIT_SWING_CAPITAL = 1_000_000.0

st.set_page_config(page_title="Desk 1: ₹10L Positional Swing", page_icon="📈", layout="wide")
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
    t_rec = sh.worksheet("trades").get_all_records()
    try:
        m_rec = sh.worksheet("agent_messages").get_all_records()
    except Exception:
        m_rec = []
    try:
        c_val = float(sh.worksheet("account").acell("B2").value or INIT_SWING_CAPITAL)
    except Exception:
        c_val = INIT_SWING_CAPITAL
    return c_val, t_rec, m_rec


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


sw_cash, sw_trades, sw_msgs = load_swing_snapshot(gcp_creds)
sw_df = pd.DataFrame(sw_trades) if sw_trades else pd.DataFrame()
msgs_df = pd.DataFrame(sw_msgs).iloc[::-1] if sw_msgs else pd.DataFrame()

open_sw = sw_df[sw_df["status"] == "OPEN"].copy() if not sw_df.empty else pd.DataFrame()
closed_sw = sw_df[sw_df["status"] != "OPEN"].copy() if not sw_df.empty else pd.DataFrame()
ltps = fetch_swing_ltps(open_sw["symbol"].astype(str).tolist() if not open_sw.empty else [])

trust_map = compute_book_and_combined_trust([], sw_trades)
open_stop_risk = compute_swing_open_downside_risk(open_sw)
sec_counts = get_sector_counts(open_sw["symbol"].astype(str).tolist() if not open_sw.empty else [])

st.markdown("## 📈 Desk 1: ₹10,00,000 Positional Equity Swing (V4.6)")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Open Swing Slots", f"{len(open_sw)} / {MAX_SWING_SLOTS}", "Paused (Over Cap)" if len(open_sw) >= MAX_SWING_SLOTS else "Active")
m2.metric("Swing Free Cash", f"₹{sw_cash:,.2f}")
m3.metric("Open Stop Risk", f"₹{open_stop_risk:,.2f}", "Downside to active SLs")
m4.metric("Swing Trust (50 Base)", f"AG1 {trust_map['SWING']['AG1_LOW_RISK']} • AG2 {trust_map['SWING']['AG2_MID_RISK']} • AG3 {trust_map['SWING']['AG3_HIGH_RISK']}")

if sec_counts:
    pills = " &nbsp; ".join([f"<b>{sec}:</b> {cnt}/{MAX_SWING_PER_SECTOR}" for sec, cnt in sorted(sec_counts.items())])
    st.markdown(f"<div class='panel-box' style='margin-bottom:10px;'>🏭 <b>Sector Concentration (Cap {MAX_SWING_PER_SECTOR}/sector):</b> &nbsp; {pills}</div>", unsafe_allow_html=True)

if not open_sw.empty:
    rows_html = [
        "<div class='panel-box' style='padding:14px;margin-bottom:12px;'>",
        "<div style='font-size:16px;font-weight:700;margin-bottom:10px;'>Active Swing Positions & Trailing Protection</div>",
        "<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;font-size:13px;'>",
        "<thead><tr style='border-bottom:1px solid #1e293b;color:#94a3b8;font-size:11px;text-transform:uppercase;'>",
        "<th style='text-align:left;padding:8px;'>Symbol</th><th style='text-align:left;padding:8px;'>Sector</th><th style='text-align:left;padding:8px;'>Tier</th>",
        "<th style='text-align:right;padding:8px;'>Qty</th><th style='text-align:right;padding:8px;'>Entry ₹</th><th style='text-align:right;padding:8px;'>LTP ₹</th>",
        "<th style='text-align:right;padding:8px;'>Stop ₹</th><th style='text-align:right;padding:8px;'>Target ₹</th><th style='text-align:center;padding:8px;'>Protection</th>",
        "<th style='text-align:right;padding:8px;'>MTM P&L ₹</th></tr></thead><tbody>",
    ]
    for _, r in open_sw.iterrows():
        s = str(r["symbol"])
        q = int(float(r["qty"]))
        e = float(r["entry"])
        sl = float(r["sl"])
        tp = float(r["target"])
        ltp = float(ltps.get(s, e))
        mtm = round((ltp - e) * q, 2)
        m_col = "#34d399" if mtm >= 0 else "#f87171"
        m_str = f"+{mtm:,.2f}" if mtm > 0 else f"{mtm:,.2f}"
        rows_html.append(
            f"<tr style='border-bottom:1px solid rgba(30,41,59,0.65);'>"
            f"<td style='padding:9px;font-weight:700;'>{s.replace('.NS','')}</td>"
            f"<td style='padding:9px;color:#94a3b8;'>{get_sector(s)}</td>"
            f"<td style='padding:9px;'>{tier_pill(str(r.get('tier','MID')))}</td>"
            f"<td style='padding:9px;text-align:right;'>{q:,}</td>"
            f"<td style='padding:9px;text-align:right;'>{e:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;font-weight:600;'>{ltp:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;'>{sl:,.2f}</td>"
            f"<td style='padding:9px;text-align:right;'>{tp:,.2f}</td>"
            f"<td style='padding:9px;text-align:center;'>{prot_pill(e, sl, q)}</td>"
            f"<td style='padding:9px;text-align:right;font-weight:700;color:{m_col};'>{m_str}</td></tr>"
        )
    rows_html.append("</tbody></table></div></div>")
    st.markdown("".join(rows_html), unsafe_allow_html=True)

if not msgs_df.empty:
    today_s = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d")
    d1, d2 = st.columns(2)
    with d1:
        st.download_button("⬇️ Swing Agent Stream CSV", data=msgs_df.to_csv(index=False).encode("utf-8"), file_name=f"swing_agent_stream_{today_s}.csv", mime="text/csv")
    with d2:
        l_df = msgs_df[msgs_df["msg_type"].astype(str).str.contains("LESSON", case=False, na=False)] if "msg_type" in msgs_df.columns else pd.DataFrame()
        st.download_button("⬇️ Swing Lessons CSV", data=l_df.to_csv(index=False).encode("utf-8"), file_name=f"swing_lessons_{today_s}.csv", mime="text/csv")
