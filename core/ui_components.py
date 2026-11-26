import time
import json
from typing import Any, Dict, List
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.attribution import compute_desk_and_agent_trust
from core.charges import compute_net_rr
from core.config import normalize_desk_key, get_desk_config, get_effective_desk_config, parse_desk_settings
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
"""


def inject_theme() -> None:
    st.markdown(DARK_OBS_CSS, unsafe_allow_html=True)


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
        f"<span class='v48-pill pill-amber'>PAPER MODE &mdash; Broker Parked (live_mode=False)</span></div>"
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
        )


def _style_watch_dataframe(df: pd.DataFrame) -> Any:
    def style_tier(val: Any) -> str:
        v = str(val).upper()
        if v == "HIGH":
            return "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"
        if v == "LOW":
            return "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;"
        return "background-color: rgba(245,158,11,0.22); color: #FCD34D; font-weight: 700;"

    def style_protection(val: Any) -> str:
        return "background-color: rgba(16,185,129,0.22); color: #6EE7B7; font-weight: 700;" if str(val).startswith("Locked") else "background-color: rgba(244,63,94,0.22); color: #FDA4AF; font-weight: 700;"

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

    return df.style.map(style_tier, subset=["TIER"]).map(style_protection, subset=["PROTECTION"]).map(style_signed, subset=["PROG %", "MTM GROSS ₹", "MTM NET ₹"])


def render_filter_watch_table(open_trades: List[Dict[str, Any]], key_prefix: str = "main", on_close_callback: Any = None) -> None:
    st.subheader("Filter Watch Table")
    c_filt, c_view = st.columns([3, 1])
    with c_filt:
        filter_choice = st.radio("Filter Watch Table", ["All Positions", "Locked Profit Only", "At Risk / Breached Only"], horizontal=True, label_visibility="collapsed", key=f"{key_prefix}_watch_filter")
    with c_view:
        compact_cards = st.toggle("Compact Card View", value=False, key=f"{key_prefix}_compact_cards")

    filtered = [t for t in open_trades if t.get("is_locked")] if filter_choice == "Locked Profit Only" else ([t for t in open_trades if not t.get("is_locked")] if filter_choice == "At Risk / Breached Only" else open_trades)
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
                    f"Qty {int(t['qty']):,} | Entry ₹{float(t['entry']):,.2f} &rarr; LTP ₹{float(t['ltp']):,.2f}</div>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:8px;'>"
                    f"<span class='{prot_cls}'>{t['protection_label']}</span>"
                    f"<span style='font-family:monospace;font-weight:700;color:{mtm_col};'>Net {'+' if mtm >= 0 else ''}{mtm:,.2f}</span></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        rows = [{
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
        } for t in filtered]
        st.dataframe(_style_watch_dataframe(pd.DataFrame(rows)), width="stretch", hide_index=True, height=min(740, 44 + len(rows) * 35))

    if on_close_callback is not None and filtered:
        with st.expander("Manual Paper Exit Controls"):
            sel_sym = st.selectbox("Select open position to close at current LTP (net of charges):", [f"{t['symbol']} | {t.get('desk','Swing')} | Qty {t['qty']} | Net MTM ₹{t['mtm_pnl']:+,.2f}" for t in filtered], key=f"{key_prefix}_close_select")
            if st.button("Confirm Paper Exit", key=f"{key_prefix}_close_btn", type="primary"):
                chosen_symbol = sel_sym.split("|")[0].strip()
                res = on_close_callback(chosen_symbol)
                if res.get("closed"):
                    st.success(f"Closed {chosen_symbol} at LTP | Net P&L: ₹{res['net_pnl']:+,.2f} ({res['realized_r']:+.2f}R)")
                    st.rerun()


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
        )


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
        )


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
    return True


def render_agent_war_room(messages: List[Dict[str, Any]], limit: int = 15, scope_desk: str = "ALL") -> None:
    sc = (scope_desk or "ALL").strip().upper()
    title_map = {
        "ALL": "5-Agent Council & Watchdog Deliberation Feed (Both Ledgers)",
        "SWING": "5-Agent Council & Watchdog Deliberation Feed — Swing Desk",
        "INTRADAY": "5-Agent Council & Watchdog Deliberation Feed — Intraday Desk",
        "FNO": "5-Agent Council & Watchdog Deliberation Feed — F&O Desk",
        "COMMODITY": "5-Agent Council & Watchdog Deliberation Feed — Commodity Desk",
        "AUDIT": "5-Agent Council & Watchdog Deliberation Feed — Audit & Backtest",
        "POSTTRADE": "5-Agent Council & Watchdog Deliberation Feed — Post-Trade & Attribution",
    }
    st.subheader(title_map.get(sc, "5-Agent Council & Watchdog Deliberation Feed"))
    filtered = [m for m in messages if _message_matches_scope(m, sc)]
    recent = sorted(filtered, key=lambda m: str(m.get("timestamp", "")), reverse=True)[:limit]
    if not recent:
        st.info(f"No agent messages logged yet for {sc}.")
        return
    for m in recent:
        sender = str(m.get("sender", ""))
        pill_cls = "pill-green" if ("AG4" in sender or "AG5" in sender) else ("pill-amber" if "WATCHDOG" in sender else ("pill-rose" if "HIGH" in sender else "pill-cyan"))
        st.markdown(
            f"<div class='v48-kpi' style='padding:10px 14px;margin-bottom:8px;'>"
            f"<div style='display:flex;flex-wrap:wrap;justify-content:space-between;font-size:11px;font-family:monospace;'>"
            f"<div><span class='v48-pill {pill_cls}'>{sender}</span>"
            f"<span style='color:#94A3B8;'>&rarr; {m.get('recipient','')} | {m.get('msg_type','')}</span></div>"
            f"<span style='color:#64748B;'>{m.get('cycle_id','')} | {m.get('timestamp','')}</span></div>"
            f"<div style='font-size:12.5px;color:#E2E8F0;margin-top:5px;'>{m.get('summary','')}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def render_market_clock_and_manual_runner(default_desk: str = "ALL") -> None:
    from core.market_data import get_nse_session_status
    from headless_runner import trigger_manual_agent_cycle

    if "cas_enabled" not in st.session_state:
        st.session_state["cas_enabled"] = True

    sess = get_nse_session_status()
    badge_color = "#10B981" if sess["is_open"] else "#F59E0B"
    cas_color = "#10B981" if st.session_state["cas_enabled"] else "#EF4444"
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:8px 12px;"
        f"border-radius:8px;background:#0F172A;border:1px solid #1E293B;margin-bottom:10px;font-family:monospace;font-size:12px;'>"
        f"<span style='color:{badge_color};font-weight:700;'>● NSE: {sess['state']}</span>"
        f"<span style='color:#E2E8F0;'>IST: {sess['time_ist']}</span>"
        f"<span style='color:#93C5FD;'>Iraq: {sess['time_iraq']}</span>"
        f"<span style='color:{cas_color};font-weight:700;'>CAS: {'ON' if st.session_state['cas_enabled'] else 'PAUSED'}</span>"
        f"<span style='color:#94A3B8;'>Hours: {sess['session_window_ist']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.expander("Manual Agent Cycle, CAS Toggle & Watchdog Trigger", expanded=False):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
        desk_opts = ["ALL", "SWING", "INTRADAY", "FNO", "COMMODITY"]
        idx_default = desk_opts.index(default_desk.upper()) if default_desk.upper() in desk_opts else 0
        sel_desk = c1.selectbox("Target Desk", desk_opts, index=idx_default, key=f"man_desk_{default_desk}")
        max_ent = c2.number_input("Max Entries", min_value=1, max_value=5, value=1, step=1, key=f"man_ent_{default_desk}")
        st.session_state["cas_enabled"] = c3.checkbox("CAS Active", value=st.session_state["cas_enabled"], key=f"cas_chk_{default_desk}")
        if c4.button("Run Agent Cycle Now", type="primary", key=f"man_run_btn_{default_desk}"):
            with st.spinner("Running agent cycle..."):
                res = trigger_manual_agent_cycle(desk=sel_desk, max_entries=int(max_ent), watchdog_bursts=1, ignore_session_gate=True, cas_enabled=bool(st.session_state["cas_enabled"]))
            st.session_state["last_manual_cycle_res"] = res
            st.rerun()
        if "last_manual_cycle_res" in st.session_state:
            _errs = st.session_state["last_manual_cycle_res"].get("errors", [])
            for _e in _errs:                                  # shown in the app (Streamlit hides uncaught exception text)
                st.error(f"{_e['desk']}: {_e['error']}")
            if not _errs:
                st.success("Completed manual cycle (Paper Mode: LIVE_MODE=false) — Ledger & Feed synced.")
            with st.expander("Last Manual Cycle JSON Output", expanded=False):
                st.json(st.session_state["last_manual_cycle_res"])


def _desk_trades(trades: List[Dict[str, Any]], cfg: Any, open_only: bool) -> List[Dict[str, Any]]:
    out = []
    for t in trades:
        if normalize_desk_key(t.get("desk", "Swing")) != cfg.key:
            continue
        if (str(t.get("status", "")).upper() == "OPEN") == open_only:
            out.append(t)
    return out


def derive_free_cash(cfg: Any, trades: List[Dict[str, Any]]) -> float:
    """Free cash = configured capital + realized P&L - cash blocked by open positions (margin-aware)."""
    realized = sum(float(t.get("pnl", 0.0) or 0.0) for t in _desk_trades(trades, cfg, open_only=False))
    blocked = sum(float(t.get("entry", 0.0) or 0.0) * int(float(t.get("qty", 0) or 0)) * cfg.margin_pct for t in _desk_trades(trades, cfg, open_only=True))
    return max(0.0, cfg.capital + realized - blocked)


def render_capital_settings(store: Any, accounts: Dict[str, Any], key_prefix: str = "dash") -> None:
    """Editable per-desk settings that are SAVED to the ledger `account` tab; the headless runner reads them every cycle."""
    st.caption("Saved to the ledger and used by the GitHub-Actions runner on its next cycle (not just this browser session).")
    new_settings: Dict[str, Dict[str, Any]] = {}
    for key in ("swing", "intraday", "fno", "commodity"):
        cfg = get_effective_desk_config(key, accounts)
        st.markdown(f"**{cfg.label}**")
        c1, c2, c3, c4, c5 = st.columns(5)
        cap = c1.number_input("Capital (₹)", min_value=0.0, step=10000.0, value=float(cfg.capital), key=f"{key_prefix}_{key}_cap")
        mp = c2.number_input("Max per position (%)", min_value=1.0, max_value=100.0, step=1.0, value=float(cfg.max_pos_pct), key=f"{key_prefix}_{key}_mp")
        rk = c3.number_input("Risk per trade (%)", min_value=0.1, max_value=5.0, step=0.1, value=float(cfg.risk_per_trade_pct), key=f"{key_prefix}_{key}_rk")
        cu = c4.number_input("Soft cushion (%)", min_value=0.0, max_value=40.0, step=1.0, value=float(cfg.soft_cushion_pct), key=f"{key_prefix}_{key}_cu")
        sl = c5.number_input("Baseline slots", min_value=1, max_value=100, step=1, value=int(cfg.baseline_slots), key=f"{key_prefix}_{key}_sl")
        new_settings[key] = {"capital": cap, "max_pos_pct": mp, "risk_per_trade_pct": rk, "soft_cushion_pct": cu, "baseline_slots": sl}
    if st.button("💾 Save settings for the runner", type="primary", key=f"{key_prefix}_save_settings"):
        store.update_account_state({"desk_settings": json.dumps(new_settings)})
        st.success("Saved. The next agent cycle will size positions with these values.")
        st.rerun()


def render_desk_workspace(store: Any, state: Dict[str, Any], desk_key: str, default_chart_symbol: str) -> None:
    accounts = state.get("accounts", {})
    cfg = get_effective_desk_config(desk_key, accounts)
    all_trades = state["trades"]
    desk_open = [t for t in all_trades if t["status"] == "OPEN" and normalize_desk_key(t.get("desk", "Swing")) == cfg.key]
    desk_closed = [t for t in all_trades if t["status"] != "OPEN" and normalize_desk_key(t.get("desk", "Swing")) == cfg.key]
    trust_data = compute_desk_and_agent_trust(all_trades)
    d_trust = trust_data["desks"][cfg.label]

    st.header(f"{cfg.label} Desk — Multi-Asset V4.8 Workspace")

    c1, c2, c3, c4, c5 = st.columns(5)
    cap = c1.number_input(f"{cfg.label} Capital (₹)", min_value=0.0, step=10000.0, value=float(cfg.capital), key=f"{cfg.key}_cap")
    max_pos = c2.number_input("Max Per-Pos Cap (%)", min_value=2.0, max_value=100.0, step=1.0, value=float(cfg.max_pos_pct), key=f"{cfg.key}_maxpos")
    risk_pct = c3.number_input("Risk Per Trade (%)", min_value=0.1, max_value=5.0, step=0.1, value=float(cfg.risk_per_trade_pct), key=f"{cfg.key}_risk")
    cushion_pct = c4.number_input("Soft Cushion (%)", min_value=0.0, max_value=40.0, step=1.0, value=float(cfg.soft_cushion_pct), key=f"{cfg.key}_cush")
    allow_tap = c5.checkbox("Allow Cushion Tap", value=cfg.allow_cushion_tap, key=f"{cfg.key}_tap")
    if st.button(f"💾 Save as {cfg.label} runner settings", key=f"{cfg.key}_save_ws"):
        merged = parse_desk_settings(accounts.get("desk_settings"))
        merged[cfg.key] = {"capital": cap, "max_pos_pct": max_pos, "risk_per_trade_pct": risk_pct, "soft_cushion_pct": cushion_pct, "allow_cushion_tap": allow_tap}
        store.update_account_state({"desk_settings": json.dumps(merged)})
        st.success("Saved - the runner uses these on its next cycle.")
        st.rerun()

    deployed = sum(float(t["entry"]) * int(t["qty"]) * cfg.margin_pct for t in desk_open)
    unreal_net = sum(float(t.get("mtm_pnl", 0.0)) for t in desk_open)
    real = sum(float(t.get("pnl", 0.0)) for t in desk_closed)

    raw_avail = max(0.0, cap + real - deployed)
    soft_buf = cap * (cushion_pct / 100.0)

    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Available Cash (Ledger)", f"₹{raw_avail:,.2f}", f"Buffer ₹{soft_buf:,.0f}")
    s2.metric("Open Deployed", f"₹{deployed:,.2f}", f"{len(desk_open)} / {cfg.baseline_slots} Slots")
    s3.metric("Unrealized Net MTM", f"₹{unreal_net:+,.2f}")
    s4.metric("Realized P&L (Closed)", f"₹{real:+,.2f}", f"{len(desk_closed)} Closed Trades")
    s5.metric(f"{cfg.label} Trust Score", f"{d_trust['trust_score']}/100 ({d_trust['delta']:+.1f})", f"Win {d_trust['win_rate']}% | Avg {d_trust['avg_r']:+.2f}R")

    render_desk_individual_agent_trust(cfg.label, d_trust)

    chart_syms = [t["symbol"] for t in desk_open] or [t["symbol"] for t in desk_closed] or [default_chart_symbol]
    sel_chart_sym = st.selectbox("Select Chart & Live Sizer Symbol", list(dict.fromkeys(chart_syms)), key=f"{cfg.key}_chart_sym")
    q = get_quote_snapshot(sel_chart_sym, default_entry=100.0)
    lot_sz = 1
    sizer_entry = float(q["ltp"])
    sizer_sl = round(max(1.0, sizer_entry - max(0.5, float(q.get("atr", sizer_entry * 0.015)))), 2)
    sizer_tgt = round(sizer_entry + 2.0 * abs(sizer_entry - sizer_sl), 2)
    sizing = compute_position_sizing(
        desk_capital=cap, deployed_capital=deployed, entry_price=sizer_entry, stop_loss=sizer_sl, target_price=sizer_tgt,
        conviction_score=85.0, max_pos_pct=max_pos, risk_per_trade_pct=risk_pct, soft_cushion_pct=cushion_pct,
        baseline_slots=cfg.baseline_slots, current_open_slots=len(desk_open), allow_cushion_tap=allow_tap,
        allow_overflow_slots=cfg.allow_overflow_slots, lot_size=lot_sz,
        max_overflow_slots=cfg.max_overflow_slots, margin_pct=cfg.margin_pct,
    )
    net_rr = compute_net_rr(sizer_entry, sizer_sl, sizer_tgt, max(1, sizing["qty"]), desk=cfg.label)
    st.info(
        f"**Live Sizer ({sel_chart_sym}):** LTP `₹{q['ltp']:,.2f}` | Raw Risk Qty `{sizing['raw_risk_qty']}` -> Final Qty `{sizing['qty']}` (Lot `{lot_sz}`) | "
        f"Notional `₹{sizing['notional']:,.0f}` | Net R:R `1:{net_rr:.2f}` | Tag: **`{sizing['allocation_tag']}`**"
    )

    bars = pd.DataFrame(get_ohlcv_bars(sel_chart_sym, q["ltp"], count=32))
    fig = go.Figure(data=[go.Candlestick(x=bars["bar"], open=bars["open"], high=bars["high"], low=bars["low"], close=bars["close"], name=sel_chart_sym)])
    fig.add_hline(y=q["pdh"], line_dash="dash", line_color="#C084FC", annotation_text=f"PDH ₹{q['pdh']}")
    fig.add_hline(y=q["pdl"], line_dash="dash", line_color="#C084FC", annotation_text=f"PDL ₹{q['pdl']}")
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0B1120", plot_bgcolor="#0F172A", height=350, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, width="stretch")

    render_filter_watch_table(open_trades=desk_open, key_prefix=cfg.key, on_close_callback=lambda sym: store.close_paper_position(sym))

    if desk_closed:
        st.subheader(f"{cfg.label} Closed / Exited Trades ({len(desk_closed)})")
        cols_show = ["timestamp", "exit_timestamp", "symbol", "tier", "status", "qty", "entry", "initial_sl", "exit_price", "gross_pnl", "charges_slippage", "pnl", "realized_r", "reason"]
        df_c = pd.DataFrame(desk_closed)
        st.dataframe(df_c[[c for c in cols_show if c in df_c.columns]], width="stretch", hide_index=True)

    render_agent_war_room(state["agent_messages"], limit=10, scope_desk=cfg.key.upper())
def render_auto_refresh_control(default_enabled: bool = False, interval_sec: int = 20, key_prefix: str = "main") -> None:
    """Optional polling loop that respects the 20s ledger cache TTL."""
    interval_sec = max(20, int(interval_sec))
    c1, c2 = st.columns([1, 3])
    enabled = c1.toggle("Auto-Refresh", value=default_enabled, key=f"{key_prefix}_autorefresh")
    sec = c2.number_input("Interval (sec)", min_value=20, max_value=300, value=interval_sec, step=5, key=f"{key_prefix}_refresh_sec")
    if enabled:
        time.sleep(int(sec))
        st.rerun()
