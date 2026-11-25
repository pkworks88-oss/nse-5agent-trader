import pandas as pd
import streamlit as st
from core.backtest import run_parity_backtest
from core.charges import compute_net_rr, estimate_roundtrip_charges
from core.config import get_desk_config, normalize_desk_key
from core.intelligence import get_desk_candidates
from core.ledger import LedgerStore, load_ledger_cached
from core.market_data import FALLBACK_QUOTES, _fetch_real_ohlcv_df, get_quote_snapshot
from core.scenario_sim import run_bootstrap_simulation
from core.stage_audit import build_8_stage_audit
from core.ui_components import render_agent_war_room, render_top_command_bar

st.set_page_config(page_title="8-Stage Audit & Time Machine — V4.8", layout="wide")
store = LedgerStore()
state = load_ledger_cached(ttl_seconds=20)
render_top_command_bar(ledger_source=state["source"], status_msg=state["status_msg"])

st.header("8-Stage Audit Trail, Historical Bootstrap & Multi-Candidate Backtester")
all_trades = state.get("trades", [])
ledger_symbols = [str(t.get("symbol", "")).strip().upper() for t in all_trades if str(t.get("symbol", "")).strip()]
fallback_ns = [k for k in FALLBACK_QUOTES.keys() if k.endswith(".NS")]
preset_choices = list(dict.fromkeys(ledger_symbols + fallback_ns + ["NIFTY 24850 CE", "GOLDPETAL MCX"]))

c_pick, c_custom, c_desk, c_tier = st.columns([2, 2, 1, 1])
picked_sym = c_pick.selectbox("Preset / Ledger Stock Selector", preset_choices, index=preset_choices.index("RELIANCE.NS") if "RELIANCE.NS" in preset_choices else 0)
custom_ns_input = c_custom.text_input("Or Type Any NSE Ticker (.NS auto-appended)", value="", placeholder="e.g. TCS, INFY.NS, SBIN, WIPRO, ITC")
selected_desk = c_desk.selectbox("Desk", ["Swing", "Intraday", "F&O", "Commodity"], index=0)
selected_tier = c_tier.selectbox("Risk Tier", ["LOW", "MID", "HIGH"], index=1)

raw_custom = custom_ns_input.strip().upper()
selected_sym = (raw_custom if (" " in raw_custom or raw_custom.endswith(" MCX") or raw_custom.endswith(".NS")) else f"{raw_custom}.NS") if raw_custom else picked_sym.strip().upper()

matched_trade = next((t for t in all_trades if str(t.get("symbol", "")).strip().upper() == selected_sym), None)
default_ref = float(matched_trade["entry"]) if matched_trade and float(matched_trade.get("entry", 0)) > 0 else 500.0
q = get_quote_snapshot(selected_sym, default_entry=default_ref)

base_entry = float(matched_trade["entry"]) if matched_trade and float(matched_trade.get("entry", 0)) > 0 else float(q["ltp"])
base_sl = float(matched_trade["initial_sl"]) if matched_trade and float(matched_trade.get("initial_sl", 0)) > 0 else round(base_entry * 0.96, 2)
base_target = float(matched_trade["target"]) if matched_trade and float(matched_trade.get("target", 0)) > 0 else round(base_entry * 1.06, 2)
base_qty = int(matched_trade["qty"]) if matched_trade and int(matched_trade.get("qty", 0)) > 0 else max(1, int(50000 // max(base_entry, 1.0)))

p1, p2, p3, p4 = st.columns(4)
entry_px = p1.number_input("Entry Price (₹)", min_value=0.05, value=base_entry, step=0.5, key=f"entry_{selected_sym}")
sl_px = p2.number_input("Initial Stop-Loss (₹)", min_value=0.05, value=base_sl, step=0.5, key=f"sl_{selected_sym}")
target_px = p3.number_input("Target Price (₹)", min_value=0.05, value=base_target, step=0.5, key=f"tgt_{selected_sym}")
qty_sim = int(p4.number_input("Simulation Qty", min_value=1, value=base_qty, step=1, key=f"qty_{selected_sym}"))

hist_returns = None
if selected_sym.endswith(".NS"):
    df_h = _fetch_real_ohlcv_df(selected_sym, period="6mo", interval="1d")
    if df_h is not None and len(df_h) >= 15:
        rets = df_h["Close"].astype(float).pct_change().dropna().tolist()
        if len(rets) >= 10:
            hist_returns = rets

charges_est = estimate_roundtrip_charges(entry_px, target_px, qty_sim, desk=selected_desk)
net_rr = compute_net_rr(entry_px, sl_px, target_px, qty_sim, desk=selected_desk)
sim = run_bootstrap_simulation(entry=entry_px, sl=sl_px, target=target_px, qty=qty_sim, charges=charges_est, historical_returns=hist_returns)

desk_cfg = get_desk_config(selected_desk)
raw_risk_qty = int((desk_cfg.capital * (desk_cfg.risk_per_trade_pct / 100.0)) // max(0.05, abs(entry_px - sl_px))) if desk_cfg.capital > 0 else qty_sim
capped_qty = min(raw_risk_qty, int((desk_cfg.capital * (desk_cfg.max_pos_pct / 100.0)) // max(0.01, entry_px))) if desk_cfg.capital > 0 else qty_sim
alloc_tag = "CAPPED_BY_MAX_POS" if raw_risk_qty > capped_qty and desk_cfg.capital > 0 else ("DESK_UNFUNDED" if desk_cfg.capital <= 0 else "STANDARD_ALLOC")

audit = build_8_stage_audit(
    symbol=selected_sym, desk=selected_desk, tier=selected_tier, entry=entry_px, sl=sl_px, target=target_px,
    rsi=float(q["rsi"]), rvol=float(q["rvol"]), pdh=float(q["pdh"]), pdl=float(q["pdl"]),
    net_rr=net_rr, sector=str(matched_trade.get("sector", "GENERAL")) if matched_trade else "GENERAL",
    sector_count=0, earnings_hours=360,
    sizing_result={"approved": desk_cfg.capital > 0, "qty": qty_sim, "raw_risk_qty": raw_risk_qty, "capped_qty": capped_qty, "notional": round(entry_px * qty_sim, 2), "allocation_tag": alloc_tag, "reason": f"Evaluated {selected_sym} on {selected_desk} desk (Net R:R 1:{net_rr:.2f}, Est. Charges ₹{charges_est:,.2f})."},
    sim_result=sim,
)

st.caption(f"Auditing **{selected_sym}** | LTP: ₹{float(q['ltp']):,.2f} | RSI: {float(q['rsi']):.1f} | RVOL: {float(q['rvol']):.2f}x | PDH: ₹{float(q['pdh']):,.2f} | PDL: ₹{float(q['pdl']):,.2f}")
for stg in audit:
    with st.expander(f"Stage {stg['stage_num']}: {stg['stage']} — [{stg['status']}] {stg['summary']}", expanded=stg["stage_num"] in (3, 7)):
        st.write(f"**Why:** {stg['why']}")
        st.code(stg["metrics_json"], language="json")

st.subheader(f"Excursion Simulator — {selected_sym} ({sim['mode']})")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Target-First Odds", f"{sim['target_first_pct']}%")
m2.metric("Stop-First Odds", f"{sim['stop_first_pct']}%")
m3.metric("Expected Net P&L", f"₹{sim['expected_net_pnl']:+,.2f}")
m4.metric("Net R:R (After Charges)", f"1:{net_rr:.2f}", f"Est. Charges ₹{charges_est:,.2f}")

st.subheader(f"Gate audit of today's {selected_desk} candidates")
st.caption("This runs the live gates on TODAY's candidates. It is NOT a performance backtest - see the Real Historical Backtest below.")
pool_cands = get_desk_candidates(normalize_desk_key(selected_desk).upper())
bt = run_parity_backtest(pool_cands, desk_capital=desk_cfg.capital, max_pos_pct=desk_cfg.max_pos_pct, risk_per_trade_pct=desk_cfg.risk_per_trade_pct, soft_cushion_pct=desk_cfg.soft_cushion_pct, baseline_slots=desk_cfg.baseline_slots, allow_cushion_tap=desk_cfg.allow_cushion_tap, allow_overflow_slots=desk_cfg.allow_overflow_slots)
st.json(bt)

st.divider()
st.header("Real Historical Backtest")
st.caption("Runs the same scoring, sizing, audit, charges, trailing-stop, rollover, time-stop, floor and breaker code as the live runner. "
           "Signals use completed bars and fill at the next bar's open; stops are tested before targets inside a bar; gaps fill at the open.")
bc1, bc2, bc3 = st.columns([1, 1, 2])
bt_desk = bc1.selectbox("Backtest desk", ["Swing (daily bars)", "Intraday (5-minute bars)"], key="bt_desk")
bt_years = bc2.slider("Years of history (swing)", 1, 5, 3, key="bt_years")
bt_floor = bc3.checkbox("Swing basket profit-floor enabled", value=True, key="bt_floor")


@st.cache_data(ttl=3600, show_spinner=False)
def _run_real_backtest(desk_label: str, years: int, floor_on: bool):
    from core.backtest import run_historical_backtest, run_intraday_mis_backtest
    if desk_label.startswith("Swing"):
        return run_historical_backtest(years=years, overrides={"basket_floor": floor_on})
    return run_intraday_mis_backtest()


if st.button("Run real backtest (downloads Yahoo history - may take a few minutes)", type="primary"):
    try:
        with st.spinner("Downloading history and replaying the strategy..."):
            st.session_state["real_bt"] = _run_real_backtest(bt_desk, bt_years, bt_floor)
    except Exception as exc:
        st.error(f"Backtest failed: {exc}")

res = st.session_state.get("real_bt")
if res:
    mt = res["metrics"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Trades", mt["trades"])
    k2.metric("Net P&L", f"₹{mt['net_pnl']:+,.0f}", f"{mt.get('return_pct', 0):+.1f}%")
    k3.metric("Profit factor", mt["profit_factor"] if mt["profit_factor"] is not None else "∞")
    k4.metric("Win rate", f"{mt['win_rate_pct']}%")
    k5.metric("Max drawdown", f"{mt.get('max_drawdown_pct', 0):.1f}%")
    g = mt["graduation"]
    (st.success if g["passed"] else st.warning)(
        f"Pre-live graduation (>=100 trades and PF >= 1.5): {'PASSED' if g['passed'] else 'NOT PASSED'} - trades>=100: {g['min_100_trades']}, PF>=1.5: {g['profit_factor_ge_1_5']}")
    if res["equity"]:
        st.line_chart(pd.DataFrame(res["equity"]).set_index("date")["equity"])
    c_a, c_b = st.columns(2)
    c_a.write("**Exit reasons**"); c_a.json(mt["exit_reasons"])
    c_b.write("**In-sample (first 60%) vs out-of-sample**"); c_b.json({"in_sample": mt.get("in_sample"), "out_of_sample": mt.get("out_of_sample"), "monte_carlo": mt.get("monte_carlo"), "benchmark_return_pct": mt.get("benchmark_return_pct")})
    with st.expander("All metrics"):
        st.json(mt)
    st.dataframe(pd.DataFrame(res["trades"]), use_container_width=True)
    st.download_button("Download trades CSV", pd.DataFrame(res["trades"]).to_csv(index=False), "backtest_trades.csv")
    st.warning("Caveats:\n\n" + "\n".join(f"- {c}" for c in res["caveats"]))

render_agent_war_room(state["agent_messages"], limit=12, scope_desk="AUDIT")
