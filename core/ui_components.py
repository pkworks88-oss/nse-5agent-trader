import json
import time
from typing import Any, Dict, List
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.attribution import compute_desk_and_agent_trust
from core.charges import compute_net_rr
from core.config import get_desk_config, get_effective_desk_config, normalize_desk_key, parse_desk_settings
from core.market_data import get_market_regime, get_ohlcv_bars, get_quote_snapshot, get_ticker_strip
from core.sizing import compute_position_sizing


DARK_OBS_CSS = """
<style>
  .stApp { background-color: #0B1120; color: #F3F4F6; }
  [data-testid="stSidebar"] { background-color: #111827; border-right: 1px solid #1E293B; }
  .v48-topbar { background: #111827; border: 1px solid #1E293B; border-radius: 14px; padding: 14px 18px; margin-bottom: 14px; }
  .v48-pill { display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-family: monospace; font-weight: 600; margin-right: 6px; }
  .pill-cyan { background: rgba(6,182,212,0.15); color: #67E8F9; border: 1px solid rgba(6,182,212,0.35); }
  .pill-green { background: rgba(16,185,129,0.15); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.35); }
  .pill-amber { background: rgba(245,158,11,0.15); color: #FCD34D; border: 1px solid rgba(245,158,11,0.35); }
  .pill-rose { background: rgba(244,63,94,0.15); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.35); }
  .v48-kpi { background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 14px 16px; }
  .v48-kpi-label { font-size: 12px; color: #94A3B8; }
  .v48-kpi-val { font-size: 22px; font-weight: 700; font-family: monospace; color: #F8FAFC; margin-top: 4px; }
  .v48-kpi-sub { font-size: 11px; font-family: monospace; color: #6EE7B7; margin-top: 3px; }
  .badge-tier-high { background: rgba(244,63,94,0.18); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-tier-mid  { background: rgba(245,158,11,0.18); color: #FCD34D; border: 1px solid rgba(245,158,11,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-tier-low  { background: rgba(16,185,129,0.18); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.4); padding: 2px 9px; border-radius: 9999px; font-size: 11px; font-weight: 700; }
  .badge-prot-lock { background: rgba(16,185,129,0.18); color: #6EE7B7; border: 1px solid rgba(16,185,129,0.45); padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
  .badge-prot-risk { background: rgba(244,63,94,0.18); color: #FDA4AF; border: 1px solid rgba(244,63,94,0.45); padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
</style>
"""[cite: 32]


def inject_theme() -> None:
    st.markdown(DARK_OBS_CSS, unsafe_allow_html=True)[cite: 32]


def render_top_command_bar(ledger_source: str = "google_sheets", status_msg: str = "", **kwargs: Any) -> None:
    inject_theme()
    reg = get_market_regime()
    src_badge = (
        f"<span class='v48-pill pill-green'>{status_msg or 'Single Ledger: Ledger'}</span>"
        if ledger_source == "google_sheets"
        else "<span class='v48-pill pill-amber'>Single Ledger: Local Snapshot</span>"
    )
    st.markdown(
        f"<div class='v48-topbar'>"
        f"<div style='display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;'>"
        f"<div><span style='font-weight:700;font-size:17px;color:#FFFFFF;'>NSE Multi-Asset AI Trading Platform</span>"
        f"<span class='v48-pill pill-cyan' style='margin-left:8px;'>Schema: v4.8</span>{src_badge}</div>"
        f"<div><span class='v48-pill pill-green'>Regime: {reg['regime']} ({reg['vix']}) | Breadth: {reg['breadth_adv_pct']}% Adv</span>"
        f"<span class='v48-pill pill-amber'>PAPER MODE — Broker Parked (live_mode=False)</span></div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    tickers = get_ticker_strip()
    cols = st.columns(len(tickers))
    for idx, t in enumerate(tickers):
        up = t["chg"] >= 0
        color = "#34D399" if up else "#FB7185"
        cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:8px 10px;'>"
            f"<div style='font-size:10px;color:#94A3B8;'>{t['sym']}</div>"
            f"<div style='font-size:13px;font-weight:700;font-family:monospace;color:#FFFFFF;'>{t['price']:,.2f}</div>"
            f"<div style='font-size:10px;font-family:monospace;color:{color};'>{'+' if up else ''}{t['chg']:.2f}%</div>"
            f"</div>",
            unsafe_allow_html=True,
        )[cite: 32]


def _style_watch_dataframe(df: pd.DataFrame) -> Any:
    def style_tier(val: Any) -> str:
        v = str(val).upper()
        if v == "HIGH":
            return "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"
        if v == "LOW":
            return "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;"
        return "background-color: rgba(245,158,11,0.22); color: #FCD34D; font-weight: 700;"

    def style_protection(val: Any) -> str:
        return (
            "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;"
            if str(val).startswith("Locked")
            else "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"
        )

    def style_signed(val: Any) -> str:
        try:
            num = float(str(val).replace("%", "").replace("+", "").replace(",", ""))
            if num > 0:
                return "color: #34D399; font-weight: 700;"
            if num < 0:
                return "color: #FB7185; font-weight: 700;"
        except Exception:
            pass
        return "color: #E2E8F0;"

    return (
        df.style.map(style_tier, subset=["TIER"])
        .map(style_protection, subset=["PROTECTION"])
        .map(style_signed, subset=["PROG %", "MTM GROSS ₹", "MTM NET ₹"])
    )[cite: 32]


def render_filter_watch_table(open_trades: List[Dict[str, Any]], key_prefix: str = "main", on_close_callback: Any = None) -> None:
    st.subheader("Filter Watch Table")
    c_filt, c_view = st.columns([3, 1])
    with c_filt:
        filter_choice = st.radio(
            "Filter Watch Table",
            ["All Positions", "Locked Profit Only", "At Risk / Breached Only"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"{key_prefix}_watch_filter",
        )
    with c_view:
        compact_cards = st.toggle("Compact Card View", value=False, key=f"{key_prefix}_compact_cards")

    filtered = (
        [t for t in open_trades if t.get("is_locked")]
        if filter_choice == "Locked Profit Only"
        else ([t for t in open_trades if not t.get("is_locked")] if filter_choice == "At Risk / Breached Only" else open_trades)
    )
    if not filtered:
        st.info("No open positions match this filter.")
        return

    if compact_cards:
        cols = st.columns(2)
        for idx, t in enumerate(filtered):
            tier = str(t.get("tier", "MID")).upper()
            tier_cls = "badge-tier-high" if tier == "HIGH" else ("badge-tier-low" if tier == "LOW" else "badge-tier-mid")
            prot_cls = "badge-prot-lock" if t.get("is_locked") else "badge-prot-risk"
            mtm = float(t.get("mtm_pnl", 0.0))
            mtm_col = "#34D399" if mtm >= 0 else "#FB7185"
            sym_clean = str(t["symbol"]).replace(".NS", "")
            with cols[idx % 2]:
                st.markdown(
                    f"<div class='v48-kpi' style='margin-bottom:10px;'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<strong style='font-family:monospace;color:#FFFFFF;'>{sym_clean} ({t.get('desk','Swing')})</strong>"
                    f"<span class='{tier_cls}'>{tier}</span></div>"
                    f"<div style='font-size:12px;font-family:monospace;color:#CBD5E1;margin-top:6px;'>"
                    f"Qty {int(t['qty']):,} | Entry ₹{float(t['entry']):,.2f} → LTP ₹{float(t['ltp']):,.2f}</div>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:8px;'>"
                    f"<span class='{prot_cls}'>{t['protection_label']}</span>"
                    f"<span style='font-family:monospace;font-weight:700;color:{mtm_col};'>Net {'+' if mtm >= 0 else ''}{mtm:,.2f}</span></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        rows = [
            {
                "SYMBOL": str(t["symbol"]).replace(".NS", ""),
                "DESK": t.get("desk", "Swing"),
                "TIER": str(t.get("tier", "MID")).upper(),
                "QTY": f"{int(t['qty']):,}",
                "ENTRY ₹": f"{float(t['entry']):,.2f}",
                "INIT SL ₹": f"{float(t.get('initial_sl', t['sl'])):,.2f}",
                "LTP ₹": f"{float(t['ltp']):,.2f}",
                "STOP ₹": f"{float(t['sl']):,.2f}",
                "TARGET ₹": f"{float(t['target']):,.2f}",
                "PROG %": f"{float(t.get('prog_pct', 0.0)):+.1f}%",
                "PROTECTION": t["protection_label"],
                "MTM GROSS ₹": f"{float(t.get('mtm_gross', t.get('mtm_pnl', 0.0))):+,.2f}",
                "MTM NET ₹": f"{float(t.get('mtm_pnl', 0.0)):+,.2f}",
            }
            for t in filtered
        ]
        st.dataframe(_style_watch_dataframe(pd.DataFrame(rows)), width="stretch", hide_index=True, height=min(740, 44 + len(rows) * 35))

    if on_close_callback is not None and filtered:
        with st.expander("Manual Paper Exit Controls"):
            sel_sym = st.selectbox(
                "Select open position to close at current LTP (net of charges):",
                [f"{t['symbol']} | {t.get('desk','Swing')} | Qty {t['qty']} | Net MTM ₹{t['mtm_pnl']:+,.2f}" for t in filtered],
                key=f"{key_prefix}_close_select",
            )
            if st.button("Confirm Paper Exit", key=f"{key_prefix}_close_btn", type="primary"):
                chosen_symbol = sel_sym.split("|")[0].strip()
                res = on_close_callback(chosen_symbol)
                if res.get("closed"):
                    st.success(f"Closed {chosen_symbol} at LTP | Net P&L: ₹{res['net_pnl']:+,.2f} ({res['realized_r']:+.2f}R)")
                    st.rerun()[cite: 32]


def render_desk_and_agent_scorecards(trust_data: Dict[str, Any]) -> None:
    st.subheader(f"Multi-Desk & 5-Agent Circular Trust Scorecards (Weighted Composite: {trust_data['composite_trust']}/100)")
    desk_cols = st.columns(4)
    for idx, desk_name in enumerate(["Swing", "Intraday", "F&O", "Commodity"]):
        d = trust_data["desks"][desk_name]
        t_val = d["trust_score"]
        delta = d["delta"]
        ring_col = "#34D399" if t_val >= 65 else ("#38BDF8" if t_val >= 50 else "#FBBF24")
        desk_cols[idx].markdown(
            f"<div class='v48-kpi'>"
            f"<div class='v48-kpi-label'>{desk_name} Desk Trust (50 Base)</div>"
            f"<div class='v48-kpi-val' style='color:{ring_col};'>{t_val}/100 <span style='font-size:12px;'>({delta:+.1f})</span></div>"
            f"<div class='v48-kpi-sub'>Closed {d['closed_count']} | Win {d['win_rate']}% | Avg {d['avg_r']:+.2f}R | Net ₹{d['realized_net_pnl']:+,.2f}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    ag_cols = st.columns(5)
    for idx, ag in enumerate(trust_data["agents"]):
        ag_cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:10px 12px;'>"
            f"<div style='font-size:10px;color:#67E8F9;font-family:monospace;'>{ag['agent_id']}</div>"
            f"<div style='font-size:17px;font-weight:700;font-family:monospace;color:#FFFFFF;margin-top:2px;'>{ag['trust_score']}/100 ({ag['delta']:+.1f})</div>"
            f"<div style='font-size:10px;color:#94A3B8;'>{ag['label']} ({ag['tier']})</div>"
            f"<div style='font-size:10px;color:#6EE7B7;font-family:monospace;margin-top:2px;'>Samples {ag.get('samples', ag.get('closed_count', 0))} | Win {ag['win_rate']}% | Avg {ag['avg_r']:+.2f}R</div>"
            f"</div>",
            unsafe_allow_html=True,
        )[cite: 32]


def render_desk_individual_agent_trust(desk_label: str, d_trust: Dict[str, Any]) -> None:
    st.subheader(f"{desk_label} Desk — Individual Agent Trust Scores (Desk Overall: {d_trust['trust_score']}/100, {d_trust['delta']:+.1f})")
    agents = d_trust.get("agents", [])
    if not agents:
        st.info(f"No agent trust breakdown available for {desk_label}.")
        return
    cols = st.columns(len(agents))
    for idx, ag in enumerate(agents):
        t_val = ag["trust_score"]
        col_hex = "#34D399" if t_val >= 65 else ("#38BDF8" if t_val >= 50 else "#FBBF24")
        cols[idx].markdown(
            f"<div class='v48-kpi' style='padding:10px 12px;'>"
            f"<div style='font-size:10px;color:#67E8F9;font-family:monospace;'>{ag['agent_id']}</div>"
            f"<div style='font-size:18px;font-weight:700;font-family:monospace;color:{col_hex};margin-top:2px;'>{t_val}/100 ({ag['delta']:+.1f})</div>"
            f"<div style='font-size:10px;color:#94A3B8;'>{ag['label']} ({ag['tier']})</div>"
            f"<div style='font-size:10px;color:#6EE7B7;font-family:monospace;margin-top:2px;'>Closed {ag['closed_count']} | Win {ag['win_rate']}% | Avg {ag['avg_r']:+.2f}R</div>"
            f"</div>",
            unsafe_allow_html=True,
        )[cite: 32]


def _message_matches_scope(msg: Dict[str, Any], scope_desk: str) -> bool:
    sc = (scope_desk or "ALL").strip().upper()
    if sc in ("", "ALL", "DASHBOARD"):
        return True
    blob = " ".join([str(msg.get(k, "")) for k in ("cycle_id", "sender", "recipient", "msg_type", "summary", "payload_json", "desk")]).lower()
    if sc == "SWING":
        return ("swing" in blob) or ("nse_5agent_ledger" in blob)
    if sc == "INTRADAY":
        return ("intraday" in blob) or ("intra-" in blob) or ("iwd-" in blob) or ("stepped_floor" in blob)
    if sc in ("FNO", "F&O"):
        return ("fno" in blob) or ("f&o" in blob) or ("nifty" in blob) or ("banknifty" in blob) or (" ce " in f" {blob} ") or (" pe " in f" {blob} ")
    if sc == "COMMODITY":
        return ("commodity" in blob) or ("mcx" in blob) or ("goldm" in blob) or ("crudeoilm" in blob)
    if sc == "AUDIT":
        return any(k in blob for k in ("audit", "backtest", "time_machine", "stage_audit", "verdict", "rejected", "approved", "gate_blocked"))
    if sc == "POSTTRADE":
        return any(k in blob for k in ("exit", "closed", "trailing_sl", "sl_hit", "target_hit", "squareoff", "attribution", "pnl", "realized_r", "watchdog", "ledger"))
    return True[cite: 32]


def render_agent_war_room(messages: List[Dict[str, Any]], limit: int = 15, scope_desk: str = "ALL") -> None:
    sc = (scope_desk or "ALL").strip().upper()
    title_map = {
        "ALL": "5-Agent Council & Watchdog Deliberation Feed (Both Ledgers)",
        "SWING": "5-Agent Council & Watchdog Deliberation Feed — Swing Desk",
        "INTRADAY": "5-Agent Council & Watchdog Deliberation Feed — Intraday Desk",
        "FNO": "5-Agent Council & Watchdog Deliberation Feed — F&O Desk",
        "COMMODITY": "5-Agent Council & Watchdog Deliberation
