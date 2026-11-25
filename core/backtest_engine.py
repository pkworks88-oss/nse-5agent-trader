"""Event-driven historical backtester.

It re-uses the LIVE strategy code (scoring, sizing, 8-stage audit, charges, trailing ladder, swing rollover /
time-stop / basket floor, regime filter, circuit breaker) so a backtest answers "how would the system I actually
run have behaved", not "how would some other simplified rule have behaved".

Timing rules (no look-ahead):
  * Signals are computed on a COMPLETED bar (daily close / 5-minute bar close).
  * Orders fill at the NEXT bar's open.
  * Within one bar the stop is tested before the target (pessimistic when both are inside the bar).
  * Gaps: a stop gapped through fills at the open (worse than the stop), not at the stop price.
  * Trailing stops are ratcheted on the bar close and only become effective from the next bar.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.charges import compute_net_rr, estimate_roundtrip_charges
from core.config import (
    DEFENSIVE_ALLOWED_TIERS, DEFENSIVE_RISK_MULT, INTRADAY_FLOOR_STEPS, MAX_POSITIONS_PER_SECTOR, MIN_CONVICTION,
    SWING_FLOOR_STEPS, SWING_ROLLOVER_BUFFER, SWING_ROLLOVER_EXT, SWING_TIME_STOP_DAYS, apply_desk_overrides, get_desk_config,
)
from core.quant_engine import classify_swing_regime, score_setup_from_indicators
from core.sizing import compute_position_sizing
from core.stage_audit import build_8_stage_audit
from core.trailing import compute_trailing_sl
from core.watchdog import _compute_stepped_profit_floor

SLOT_MIN = 5
FIRST_MIN = 9 * 60 + 15
N_SLOTS = 75                      # 09:15 .. 15:25 five-minute bars
EOD_MIN = 15 * 60 + 15
ENTRY_CUTOFF_MIN = 15 * 60


# ----------------------------------------------------------------------------- indicators
def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised version of core.quant_engine.analyze_ohlcv_indicators (one row per bar, same formulas)."""
    o, h, l, c = (df[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(1.0, index=df.index)
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()          # noqa: E731
    delta = c.diff()
    ag = delta.clip(lower=0.0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    al = (-delta.clip(upper=0.0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + ag / al.replace(0.0, np.nan))
    rsi = rsi.where(~((al == 0.0) & (ag > 0.0)), 100.0).fillna(55.0)
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, min_periods=1, adjust=False).mean().clip(lower=0.05)
    up, down = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_a = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean().replace(0.0, np.nan)
    pdi = 100.0 * plus_dm.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean() / atr_a
    mdi = 100.0 * minus_dm.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean() / atr_a
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    adx = dx.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean().fillna(22.0)
    tp = (h + l + c) / 3.0
    vsum = v.rolling(24, min_periods=1).sum()
    vwap = ((tp * v).rolling(24, min_periods=1).sum() / vsum.replace(0.0, np.nan)).fillna(tp)
    vavg = v.rolling(20, min_periods=1).mean()
    prev_avg = vavg.shift(1)
    rvol = (v / prev_avg).where((prev_avg > 0) & (v > 0), 1.20).fillna(1.25)
    out = pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "ema9": ema(c, 9), "ema20": ema(c, 20), "ema50": ema(c, 50),
        "rsi": rsi, "atr": atr, "adx": adx, "vwap": vwap, "rvol": rvol, "vavg20": vavg,
        "pdh": h.shift(1), "pdl": l.shift(1), "prev_close": pc,
    }, index=df.index)
    out["chg_pct"] = (out["close"] - out["prev_close"]) / out["prev_close"].clip(lower=0.01) * 100.0
    return out


def parity_check(df: pd.DataFrame) -> Dict[str, float]:
    """Max abs difference between the vectorised indicators and the live analyze_ohlcv_indicators() at the last bar."""
    from core.quant_engine import analyze_ohlcv_indicators
    live = analyze_ohlcv_indicators(df)
    row = precompute_indicators(df).iloc[-1]
    keys = ["ema9", "ema20", "ema50", "rsi", "atr", "adx", "vwap", "rvol", "pdh", "pdl", "chg_pct"]
    return {k: abs(float(row[k]) - float(live[k])) for k in keys}


# ----------------------------------------------------------------------------- helpers
def _naive_index(idx: Any) -> pd.DatetimeIndex:
    i = pd.DatetimeIndex(idx)
    return i.tz_localize(None) if i.tz is None else i.tz_convert("Asia/Kolkata").tz_localize(None)


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d.index = _naive_index(d.index)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d.dropna(subset=["Open", "High", "Low", "Close"])


def _make_trade(pos: Dict[str, Any], status: str, fill: float, exit_ts: Any, desk_label: str, bars_held: int) -> Dict[str, Any]:
    qty, entry = pos["qty"], pos["entry"]
    gross = (fill - entry) * qty
    charges = estimate_roundtrip_charges(entry, fill, qty, desk=desk_label)
    net = gross - charges
    return {
        "symbol": pos["symbol"], "tier": pos["tier"], "sector": pos["sector"], "entry_ts": str(pos["entry_ts"]), "exit_ts": str(exit_ts),
        "entry": round(entry, 2), "exit": round(fill, 2), "qty": qty, "init_sl": round(pos["init_sl"], 2),
        "gross_pnl": round(gross, 2), "charges": round(charges, 2), "pnl": round(net, 2),
        "r": round((fill - entry) / max(0.05, abs(entry - pos["init_sl"])), 2), "bars_held": bars_held,
        "status": status, "conviction": pos["conviction"], "rolled": bool(pos.get("rolled", False)),
    }


def _mtm_net(pos: Dict[str, Any], px: float, desk_label: str) -> float:
    return (px - pos["entry"]) * pos["qty"] - estimate_roundtrip_charges(pos["entry"], px, pos["qty"], desk=desk_label)


def _sector_counts(open_pos: Dict[str, Dict[str, Any]], extra: List[Dict[str, Any]] | None = None) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in list(open_pos.values()) + list(extra or []):
        out[p["sector"]] = out.get(p["sector"], 0) + 1
    return out


def _evaluate_candidate(sym: str, meta: Dict[str, Any], q: Dict[str, float], cfg: Any, desk_up: str, eff_cap: float, deployed: float,
                        slots: int, sector_counts: Dict[str, int], risk_pct: float) -> Optional[Dict[str, Any]]:
    """Run the live gate chain (score -> sizing -> net R:R -> 8-stage audit). Returns a signal dict or None."""
    scored = score_setup_from_indicators(q, desk=desk_up)
    conv = int(scored["conviction"])
    if conv < MIN_CONVICTION:
        return None
    sizing = compute_position_sizing(
        desk_capital=eff_cap, deployed_capital=deployed, entry_price=scored["entry"], stop_loss=scored["sl"], target_price=scored["target"],
        conviction_score=conv, max_pos_pct=cfg.max_pos_pct, risk_per_trade_pct=risk_pct, soft_cushion_pct=cfg.soft_cushion_pct,
        baseline_slots=cfg.baseline_slots, current_open_slots=slots, sector=meta["sector"], sector_counts=sector_counts,
        max_per_sector=MAX_POSITIONS_PER_SECTOR, allow_cushion_tap=cfg.allow_cushion_tap, allow_overflow_slots=cfg.allow_overflow_slots,
        lot_size=1, atr=q["atr"], beta=meta.get("beta"), max_overflow_slots=cfg.max_overflow_slots, margin_pct=cfg.margin_pct,
    )
    net_rr = compute_net_rr(scored["entry"], scored["sl"], scored["target"], max(1, sizing["qty"]), desk=cfg.label)
    audit = build_8_stage_audit(
        symbol=sym, desk=cfg.label, tier=meta["tier"], entry=scored["entry"], sl=scored["sl"], target=scored["target"],
        rsi=float(q["rsi"]), rvol=float(q["rvol"]), pdh=float(q["pdh"]), pdl=float(q["pdl"]), net_rr=net_rr,
        sector=meta["sector"], sector_count=sector_counts.get(meta["sector"], 0), earnings_hours=999, sizing_result=sizing, sim_result={},
    )
    if audit[6]["status"] != "APPROVED":
        return None
    return {"symbol": sym, "sl": scored["sl"], "target": scored["target"], "conviction": conv, "tier": meta["tier"], "sector": meta["sector"],
            "atr": float(q["atr"]), "beta": meta.get("beta"), "cash_req": sizing.get("cash_required", sizing["notional"])}


def _fill_pending(sig: Dict[str, Any], open_px: float, cfg: Any, eff_cap: float, deployed: float, open_pos: Dict[str, Dict[str, Any]],
                  risk_pct: float, ts: Any, idx: int, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not np.isfinite(open_px) or open_px <= sig["sl"] or open_px >= sig["target"]:
        return None                                    # gap invalidated the setup
    sizing = compute_position_sizing(
        desk_capital=eff_cap, deployed_capital=deployed, entry_price=open_px, stop_loss=sig["sl"], target_price=sig["target"],
        conviction_score=sig["conviction"], max_pos_pct=cfg.max_pos_pct, risk_per_trade_pct=risk_pct, soft_cushion_pct=cfg.soft_cushion_pct,
        baseline_slots=cfg.baseline_slots, current_open_slots=len(open_pos), sector=sig["sector"], sector_counts=_sector_counts(open_pos),
        max_per_sector=MAX_POSITIONS_PER_SECTOR, allow_cushion_tap=cfg.allow_cushion_tap, allow_overflow_slots=cfg.allow_overflow_slots,
        lot_size=1, atr=sig["atr"], beta=sig["beta"], max_overflow_slots=cfg.max_overflow_slots, margin_pct=cfg.margin_pct,
    )
    if not sizing["approved"]:
        return None
    return {"symbol": sig["symbol"], "entry": open_px, "sl": sig["sl"], "init_sl": sig["sl"], "target": sig["target"], "init_target": sig["target"],
            "qty": sizing["qty"], "cash": sizing.get("cash_required", sizing["notional"]), "entry_idx": idx, "entry_ts": ts, "tier": sig["tier"],
            "sector": sig["sector"], "conviction": sig["conviction"], "rolled": False}


# ----------------------------------------------------------------------------- metrics
def compute_metrics(trades: List[Dict[str, Any]], equity: pd.Series, capital: float, benchmark: Optional[pd.Series] = None,
                    periods_per_year: int = 252, mc_runs: int = 1000) -> Dict[str, Any]:
    n = len(trades)
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    rr = np.array([t["r"] for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    m: Dict[str, Any] = {
        "trades": n, "win_rate_pct": round(100.0 * len(wins) / n, 2) if n else 0.0,
        "net_pnl": round(float(pnl.sum()), 2), "gross_profit": round(gp, 2), "gross_loss": round(-gl, 2),
        "profit_factor": round(pf, 3) if math.isfinite(pf) else None,
        "expectancy_rs": round(float(pnl.mean()), 2) if n else 0.0, "expectancy_r": round(float(rr.mean()), 3) if n else 0.0,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0, "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "best_trade": round(float(pnl.max()), 2) if n else 0.0, "worst_trade": round(float(pnl.min()), 2) if n else 0.0,
        "total_charges": round(float(sum(t["charges"] for t in trades)), 2),
        "avg_bars_held": round(float(np.mean([t["bars_held"] for t in trades])), 2) if n else 0.0,
    }
    ex: Dict[str, int] = {}
    for t in trades:
        ex[t["status"]] = ex.get(t["status"], 0) + 1
    m["exit_reasons"] = ex

    if len(equity) >= 2:
        eq = equity.astype(float)
        peak = eq.cummax()
        dd = eq - peak
        years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
        total_ret = eq.iloc[-1] / capital - 1.0
        rets = eq.pct_change().dropna()
        m.update({
            "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "years": round(years, 2),
            "return_pct": round(100.0 * total_ret, 2),
            "cagr_pct": round(100.0 * ((1.0 + total_ret) ** (1.0 / years) - 1.0), 2) if (1.0 + total_ret) > 0 else -100.0,
            "max_drawdown": round(float(dd.min()), 2), "max_drawdown_pct": round(100.0 * float((dd / peak).min()), 2),
            "sharpe": round(float(rets.mean() / rets.std() * math.sqrt(periods_per_year)), 2) if len(rets) > 2 and rets.std() > 0 else 0.0,
        })
        m["calmar"] = round(m["cagr_pct"] / abs(m["max_drawdown_pct"]), 2) if m["max_drawdown_pct"] < 0 else None
        m["yearly_return_pct"] = {str(y): round(100.0 * float(g.iloc[-1] / g.iloc[0] - 1.0), 2) for y, g in eq.groupby(eq.index.year) if len(g) > 1}
    if benchmark is not None and len(benchmark) >= 2 and "years" in m:
        b = benchmark.astype(float)
        br = b.iloc[-1] / b.iloc[0] - 1.0
        m["benchmark_return_pct"] = round(100.0 * br, 2)
        m["benchmark_cagr_pct"] = round(100.0 * ((1.0 + br) ** (1.0 / m["years"]) - 1.0), 2)

    if n >= 10 and "start" in m:                       # in-sample / out-of-sample split at 60% of the calendar span
        t0, t1 = pd.Timestamp(m["start"]), pd.Timestamp(m["end"])
        cut = t0 + (t1 - t0) * 0.6
        def part(sel: List[Dict[str, Any]]) -> Dict[str, Any]:
            p = np.array([t["pnl"] for t in sel], dtype=float)
            if not len(p):
                return {"trades": 0}
            g, l_ = p[p > 0].sum(), -p[p <= 0].sum()
            return {"trades": len(p), "win_rate_pct": round(100.0 * (p > 0).mean(), 1), "net_pnl": round(float(p.sum()), 2),
                    "profit_factor": round(float(g / l_), 2) if l_ > 0 else None, "expectancy_r": round(float(np.mean([t["r"] for t in sel])), 3)}
        m["in_sample"] = part([t for t in trades if pd.Timestamp(t["entry_ts"][:10]) <= cut])
        m["out_of_sample"] = part([t for t in trades if pd.Timestamp(t["entry_ts"][:10]) > cut])

    if n >= 20:                                        # trade-order bootstrap: how bad could the drawdown plausibly be?
        rng = np.random.default_rng(48)
        dds, ends = [], []
        for _ in range(mc_runs):
            path = np.cumsum(rng.choice(pnl, size=n, replace=True))
            dds.append(float((path - np.maximum.accumulate(np.concatenate([[0.0], path]))[1:]).min()))
            ends.append(float(path[-1]))
        m["monte_carlo"] = {"drawdown_95pct_worst": round(float(np.percentile(dds, 5)), 2), "prob_loss_pct": round(100.0 * float(np.mean(np.array(ends) < 0)), 1)}

    m["graduation"] = {"min_100_trades": n >= 100, "profit_factor_ge_1_5": bool(pf >= 1.5 if math.isfinite(pf) else gp > 0),
                       "passed": bool(n >= 100 and (pf >= 1.5 if math.isfinite(pf) else gp > 0))}
    return m


CAVEATS_COMMON = [
    "Universe = today's hard-coded stock list (survivorship / look-ahead bias in WHICH stocks are tested).",
    "Earnings blackout, news and Gemini adjustments cannot be replayed; they are not applied.",
    "Prices are split/dividend adjusted (auto_adjust). Slippage is the flat model inside the charges function.",
    "Daily-bar fills are pessimistic inside a bar (stop before target) but cannot model intraday order-book depth.",
]


# ----------------------------------------------------------------------------- daily engine (swing)
def run_daily_backtest(data: Dict[str, pd.DataFrame], meta: Dict[str, Dict[str, Any]], index_df: Optional[pd.DataFrame] = None,
                       vix_df: Optional[pd.DataFrame] = None, desk: str = "swing", capital: Optional[float] = None,
                       overrides: Optional[Dict[str, Any]] = None, start: Optional[str] = None, end: Optional[str] = None,
                       max_entries_per_day: int = 3, warmup: int = 60, use_regime: bool = True) -> Dict[str, Any]:
    """Swing-style backtest on daily bars. meta[symbol] = {tier, sector, beta}."""
    cfg = apply_desk_overrides(get_desk_config(desk), overrides or {})
    desk_up = cfg.key.upper()
    cap0 = float(capital if capital is not None else cfg.capital)
    clean = {s: _clean_ohlcv(d) for s, d in data.items() if d is not None and len(d) > warmup + 5}
    if not clean:
        raise ValueError("No usable price history.")
    cal = sorted(set().union(*[set(d.index) for d in clean.values()]))
    if index_df is not None:
        cal = sorted(set(cal) | set(_clean_ohlcv(index_df).index))
    cal = pd.DatetimeIndex(cal)
    if start:
        cal = cal[cal >= pd.Timestamp(start)]
    if end:
        cal = cal[cal <= pd.Timestamp(end)]
    full_cal = pd.DatetimeIndex(sorted(set().union(*[set(d.index) for d in clean.values()])))
    N = len(cal)

    A: Dict[str, Dict[str, np.ndarray]] = {}
    for s, d in clean.items():
        ind = precompute_indicators(d).reindex(cal)
        n_before = d.index.searchsorted(cal[0]) if len(cal) else 0
        A[s] = {k: ind[k].to_numpy(dtype=float) for k in ind.columns}
        A[s]["_bars_before"] = n_before + np.cumsum(np.isfinite(A[s]["close"]))   # bars of history up to and including i

    regime_arr = None
    if index_df is not None and use_regime and cfg.key == "swing":
        ic = _clean_ohlcv(index_df)["Close"].astype(float)
        ncl = ic.reindex(cal).ffill()
        sma = ic.rolling(20).mean().reindex(cal).ffill()
        chg = ic.pct_change().mul(100.0).reindex(cal)
        vx = _clean_ohlcv(vix_df)["Close"].astype(float).reindex(cal).ffill() if vix_df is not None else None
        regime_arr = (ncl.to_numpy(), sma.to_numpy(), chg.to_numpy(), vx.to_numpy() if vx is not None else None)

    open_pos: Dict[str, Dict[str, Any]] = {}
    pending: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    realized_cum, swing_peak = 0.0, 0.0
    eq_dates: List[pd.Timestamp] = []
    eq_vals: List[float] = []
    regime_days = {"NORMAL": 0, "DEFENSIVE": 0}
    block_counts = {"breaker_days": 0, "floor_liquidations": 0, "breaker_liquidations": 0}

    day_new_realized = 0.0

    def eff_capital() -> float:
        return max(0.25 * cap0, cap0 + realized_cum) if cap0 > 0 else 0.0

    def close_pos(sym: str, status: str, fill: float, i: int) -> None:
        nonlocal realized_cum, day_realized, day_new_realized
        pos = open_pos.pop(sym)
        tr = _make_trade(pos, status, fill, cal[i], cfg.label, i - pos["entry_idx"])
        trades.append(tr)
        realized_cum += tr["pnl"]
        day_realized += tr["pnl"]
        if pos["entry_idx"] == i:
            day_new_realized += tr["pnl"]

    for i in range(0, N):
        day_realized = 0.0
        day_new_realized = 0.0
        risk_eff = cfg.risk_per_trade_pct
        # --- 1) fill orders queued at the previous close, at today's open
        for sig in pending:
            s = sig["symbol"]
            if s in open_pos:
                continue
            pos = _fill_pending(sig, A[s]["open"][i], cfg, eff_capital(), sum(p["cash"] for p in open_pos.values()), open_pos, sig["risk_pct"], cal[i], i, meta.get(s, {}))
            if pos:
                open_pos[s] = pos
        pending = []

        # --- 2) manage open positions on today's bar
        for s in list(open_pos.keys()):
            pos, a = open_pos[s], A[s]
            o, h, l, c = a["open"][i], a["high"][i], a["low"][i], a["close"][i]
            if not (np.isfinite(o) and np.isfinite(c)):
                continue
            sl, tgt, entry = pos["sl"], pos["target"], pos["entry"]
            if o <= sl:
                close_pos(s, "TRAILING_SL_HIT" if sl > entry else "SL_HIT", o, i); continue
            if l <= sl:
                close_pos(s, "TRAILING_SL_HIT" if sl > entry else "SL_HIT", sl, i); continue
            if o >= tgt or h >= tgt:
                if cfg.key == "swing" and not pos["rolled"]:
                    new_t = round(tgt + SWING_ROLLOVER_EXT * (tgt - entry), 2)
                    roll_sl = round(max(sl, tgt * (1.0 - SWING_ROLLOVER_BUFFER)), 2)
                    pos.update({"target": new_t, "sl": roll_sl, "rolled": True})
                    if o >= new_t:
                        close_pos(s, "TARGET_HIT", o, i); continue
                    if l <= roll_sl:                              # pessimistic: assume the dip came after the high
                        close_pos(s, "TRAILING_SL_HIT", roll_sl, i); continue
                else:
                    close_pos(s, "TARGET_HIT", max(o, tgt), i); continue
            # ratchet on close (effective next bar)
            span = abs(pos["target"] - entry)
            prog = (c - entry) / max(0.05, span) * 100.0
            per_sh = estimate_roundtrip_charges(entry, max(c, entry), pos["qty"], desk=cfg.label) / max(1, pos["qty"])
            cand = compute_trailing_sl("LONG", entry, pos["sl"], span, c, prog, cfg.trail_be_pct, cfg.trail_step_pct, per_sh)
            if cand is not None:
                pos["sl"] = cand
            if cfg.key == "swing" and (i - pos["entry_idx"]) >= SWING_TIME_STOP_DAYS and pos["sl"] <= entry:
                close_pos(s, "TIME_STOP", c, i)

        # --- 3) end-of-day protections
        def last_px(sym: str) -> float:
            v = A[sym]["close"][i]
            return float(v) if np.isfinite(v) else float(open_pos[sym]["entry"])
        basket = sum(_mtm_net(p, last_px(s), cfg.label) for s, p in open_pos.items())
        if cfg.key == "swing":
            # Swing breaker = TODAY's change of the open book (vs prior close; vs entry if opened today) + P&L of trades opened and closed today.
            day_chg = day_new_realized
            for s_, p_ in open_pos.items():
                ref = p_["entry"] if p_["entry_idx"] == i else (float(A[s_]["close"][i - 1]) if (i > 0 and np.isfinite(A[s_]["close"][i - 1])) else p_["entry"])
                day_chg += (last_px(s_) - ref) * p_["qty"]
            desk_pnl = day_chg
        else:
            desk_pnl = day_realized + basket                       # same definition as the live runner
        entries_blocked = cfg.daily_loss_limit < 0 and desk_pnl <= cfg.daily_loss_limit
        if entries_blocked:
            block_counts["breaker_days"] += 1
            if cfg.breaker_liquidates:
                for s in list(open_pos.keys()):
                    close_pos(s, "CIRCUIT_BREAKER_SQUAREOFF", last_px(s), i)
                block_counts["breaker_liquidations"] += 1
                basket = 0.0
        if cfg.key == "swing" and cfg.basket_floor:
            swing_peak = max(swing_peak, basket, 0.0) if open_pos else 0.0
            floor = _compute_stepped_profit_floor(swing_peak, SWING_FLOOR_STEPS)
            if open_pos and floor > 0 and basket < floor:
                for s in list(open_pos.keys()):
                    close_pos(s, "PROFIT_FLOOR_SQUAREOFF", last_px(s), i)
                block_counts["floor_liquidations"] += 1
                basket, swing_peak = 0.0, 0.0
        eq_dates.append(cal[i])
        eq_vals.append(cap0 + realized_cum + basket)

        # --- 4) signals on today's completed bar -> orders for tomorrow's open
        if i >= N - 1 or entries_blocked:
            continue
        defensive = False
        if regime_arr is not None:
            ncl, sma, chg, vx = regime_arr
            if np.isfinite(ncl[i]) and np.isfinite(sma[i]):
                reg = classify_swing_regime(float(ncl[i]), float(sma[i]), float(chg[i]) if np.isfinite(chg[i]) else 0.0,
                                            float(vx[i]) if (vx is not None and np.isfinite(vx[i])) else None)
                defensive = reg["defensive"]
                regime_days["DEFENSIVE" if defensive else "NORMAL"] += 1
        risk_eff = cfg.risk_per_trade_pct * (DEFENSIVE_RISK_MULT if defensive else 1.0)
        scored_list: List[Tuple[int, str, Dict[str, float]]] = []
        for s, a in A.items():
            if s in open_pos or a["_bars_before"][i] < warmup or not np.isfinite(a["close"][i]) or not np.isfinite(a["atr"][i]):
                continue
            q = {k: float(a[k][i]) for k in ("atr", "rsi", "adx", "rvol", "vwap", "ema9", "ema20", "ema50", "pdh", "pdl")}
            q["ltp"] = float(a["close"][i])
            if not all(np.isfinite(v) for v in q.values()):
                continue
            scored_list.append((int(score_setup_from_indicators(q, desk=desk_up)["conviction"]), s, q))
        scored_list.sort(key=lambda x: (-x[0], x[1]))
        queued: List[Dict[str, Any]] = []
        for conv, s, q in scored_list:
            if len(queued) >= max_entries_per_day or conv < MIN_CONVICTION:
                break
            m_ = meta.get(s, {"tier": "MID", "sector": "GENERAL", "beta": 1.0})
            if defensive and str(m_["tier"]).upper() not in DEFENSIVE_ALLOWED_TIERS:
                continue
            deployed = sum(p["cash"] for p in open_pos.values()) + sum(p["cash_req"] for p in queued)
            sig = _evaluate_candidate(s, m_, q, cfg, desk_up, eff_capital(), deployed, len(open_pos) + len(queued),
                                      _sector_counts(open_pos, [{"sector": x["sector"]} for x in queued]), risk_eff)
            if sig:
                sig["risk_pct"] = risk_eff
                queued.append(sig)
        pending = queued

    for s in list(open_pos.keys()):                                # mark the end of the test
        px = float(A[s]["close"][N - 1]) if np.isfinite(A[s]["close"][N - 1]) else open_pos[s]["entry"]
        day_realized = 0.0
        close_pos(s, "END_OF_TEST", px, N - 1)
    equity = pd.Series(eq_vals, index=pd.DatetimeIndex(eq_dates))
    if len(equity):
        equity.iloc[-1] = cap0 + realized_cum
    bench = None
    if index_df is not None:
        bench = _clean_ohlcv(index_df)["Close"].astype(float).reindex(cal).ffill().dropna()
    metrics = compute_metrics(trades, equity, cap0, bench)
    metrics["regime_days"] = regime_days
    metrics["protections"] = block_counts
    return {"engine": "daily", "desk": cfg.label, "capital": cap0, "settings": {"risk_per_trade_pct": cfg.risk_per_trade_pct, "max_pos_pct": cfg.max_pos_pct,
            "baseline_slots": cfg.baseline_slots, "max_overflow_slots": cfg.max_overflow_slots, "trail_be_pct": cfg.trail_be_pct, "trail_step_pct": cfg.trail_step_pct,
            "max_entries_per_day": max_entries_per_day}, "metrics": metrics, "trades": trades,
            "equity": [{"date": str(d.date()), "equity": round(float(v), 2)} for d, v in equity.items()],
            "caveats": CAVEATS_COMMON + ["Live VWAP is the 1-minute session VWAP; daily bars use a 24-day volume-weighted average, so the '+5 above VWAP' score differs slightly."]}


# ----------------------------------------------------------------------------- intraday engine (5-minute bars)
def run_intraday_backtest(intraday: Dict[str, pd.DataFrame], daily: Dict[str, pd.DataFrame], meta: Dict[str, Dict[str, Any]], desk: str = "intraday",
                          capital: Optional[float] = None, overrides: Optional[Dict[str, Any]] = None, max_entries_per_cycle: int = 1,
                          warmup: int = 60) -> Dict[str, Any]:
    """Intraday-equity backtest on 5-minute bars (Yahoo only serves ~60 days of 5m history, so samples are small)."""
    cfg = apply_desk_overrides(get_desk_config(desk), overrides or {})
    desk_up = cfg.key.upper()
    cap0 = float(capital if capital is not None else cfg.capital)
    syms = [s for s in intraday if s in daily and len(intraday[s]) > 100 and len(daily[s]) > warmup + 5]
    if not syms:
        raise ValueError("No symbols with both 5-minute and daily history.")

    dind = {s: precompute_indicators(_clean_ohlcv(daily[s])) for s in syms}
    ddates = {s: dind[s].index.to_numpy() for s in syms}
    day_arrays: Dict[str, Dict[pd.Timestamp, Dict[str, np.ndarray]]] = {}
    all_days: set = set()
    for s in syms:
        d5 = _clean_ohlcv(intraday[s])
        slot = ((d5.index.hour * 60 + d5.index.minute) - FIRST_MIN) // SLOT_MIN
        d5 = d5[(slot >= 0) & (slot < N_SLOTS)]
        slot = slot[(slot >= 0) & (slot < N_SLOTS)]
        per: Dict[pd.Timestamp, Dict[str, np.ndarray]] = {}
        for day, g in d5.groupby(d5.index.normalize()):
            arr = {k: np.full(N_SLOTS, np.nan) for k in ("o", "h", "l", "c", "v")}
            sl_ = slot[d5.index.normalize() == day]
            arr["o"][sl_], arr["h"][sl_], arr["l"][sl_], arr["c"][sl_] = g["Open"].to_numpy(), g["High"].to_numpy(), g["Low"].to_numpy(), g["Close"].to_numpy()
            arr["v"][sl_] = g["Volume"].to_numpy() if "Volume" in g.columns else 1.0
            tp = (arr["h"] + arr["l"] + arr["c"]) / 3.0
            v0 = np.nan_to_num(arr["v"], nan=0.0)
            arr["cumv"] = np.cumsum(v0)
            arr["vwap"] = np.where(np.cumsum(v0) > 0, np.cumsum(np.nan_to_num(tp) * v0) / np.maximum(np.cumsum(v0), 1e-9), tp)
            per[day] = arr
        day_arrays[s] = per
        all_days |= set(per.keys())
    days = sorted(all_days)

    trades: List[Dict[str, Any]] = []
    realized_cum = 0.0
    eq_dates, eq_vals = [], []
    prot = {"breaker_days": 0, "floor_days": 0}

    for day in days:
        prev_row: Dict[str, Dict[str, float]] = {}
        for s in syms:
            if day not in day_arrays[s]:
                continue
            j = int(np.searchsorted(ddates[s], np.datetime64(day))) - 1
            if j < warmup:
                continue
            r = dind[s].iloc[j]
            prev_row[s] = {k: float(r[k]) for k in ("atr", "rsi", "adx", "ema9", "ema20", "ema50", "vavg20")}
            prev_row[s].update({"prev_close": float(r["close"]), "pdh": float(r["high"]), "pdl": float(r["low"])})
        if not prev_row:
            continue

        open_pos: Dict[str, Dict[str, Any]] = {}
        pending: List[Dict[str, Any]] = []
        day_realized, peak, locked = 0.0, 0.0, False
        eff_cap = max(0.25 * cap0, cap0 + realized_cum) if cap0 > 0 else 0.0

        def close_pos(sym: str, status: str, fill: float, slot_i: int) -> None:
            nonlocal realized_cum, day_realized
            pos = open_pos.pop(sym)
            tr = _make_trade(pos, status, fill, f"{day.date()} {(FIRST_MIN + SLOT_MIN * slot_i) // 60:02d}:{(FIRST_MIN + SLOT_MIN * slot_i) % 60:02d}", cfg.label, slot_i - pos["entry_idx"])
            trades.append(tr)
            realized_cum += tr["pnl"]
            day_realized += tr["pnl"]

        def px_at(sym: str, k: int) -> float:
            arr = day_arrays[sym][day]["c"]
            for kk in range(k, -1, -1):
                if np.isfinite(arr[kk]):
                    return float(arr[kk])
            return float(open_pos[sym]["entry"])

        for k in range(N_SLOTS):
            mins = FIRST_MIN + SLOT_MIN * k
            if mins >= EOD_MIN:
                for s in list(open_pos.keys()):
                    ov = day_arrays[s][day]["o"][k]
                    close_pos(s, "EOD_SQUAREOFF", float(ov) if np.isfinite(ov) else px_at(s, k), k)
                break
            for sig in pending:
                s = sig["symbol"]
                if s in open_pos or s not in day_arrays or day not in day_arrays[s]:
                    continue
                pos = _fill_pending(sig, day_arrays[s][day]["o"][k], cfg, eff_cap, sum(p["cash"] for p in open_pos.values()), open_pos, cfg.risk_per_trade_pct, day, k, meta.get(s, {}))
                if pos:
                    open_pos[s] = pos
            pending = []

            for s in list(open_pos.keys()):
                a = day_arrays[s][day]
                o, h, l, c = a["o"][k], a["h"][k], a["l"][k], a["c"][k]
                if not np.isfinite(c):
                    continue
                pos = open_pos[s]
                sl, tgt, entry = pos["sl"], pos["target"], pos["entry"]
                if o <= sl:
                    close_pos(s, "TRAILING_SL_HIT" if sl > entry else "SL_HIT", float(o), k); continue
                if l <= sl:
                    close_pos(s, "TRAILING_SL_HIT" if sl > entry else "SL_HIT", sl, k); continue
                if h >= tgt:
                    close_pos(s, "TARGET_HIT", max(float(o), tgt), k); continue
                span = abs(tgt - entry)
                prog = (c - entry) / max(0.05, span) * 100.0
                per_sh = estimate_roundtrip_charges(entry, max(c, entry), pos["qty"], desk=cfg.label) / max(1, pos["qty"])
                cand = compute_trailing_sl("LONG", entry, sl, span, float(c), prog, cfg.trail_be_pct, cfg.trail_step_pct, per_sh)
                if cand is not None:
                    pos["sl"] = cand

            basket = sum(_mtm_net(p, px_at(s, k), cfg.label) for s, p in open_pos.items())
            desk_pnl = day_realized + basket
            peak = max(peak, desk_pnl, 0.0)
            floor = _compute_stepped_profit_floor(peak)
            tripped = None
            if cfg.daily_loss_limit < 0 and desk_pnl <= cfg.daily_loss_limit:
                tripped = "CIRCUIT_BREAKER_SQUAREOFF"
                prot["breaker_days"] += 1 if not locked else 0
            elif floor > 0 and desk_pnl < floor:
                tripped = "PROFIT_FLOOR_SQUAREOFF"
                prot["floor_days"] += 1 if not locked else 0
            if tripped:
                locked = True
                if tripped == "PROFIT_FLOOR_SQUAREOFF" or cfg.breaker_liquidates:
                    for s in list(open_pos.keys()):
                        close_pos(s, tripped, px_at(s, k), k)
            if locked or (mins + SLOT_MIN) > ENTRY_CUTOFF_MIN:
                continue

            cand_list: List[Tuple[int, str, Dict[str, float]]] = []
            frac = min(1.0, max(0.10, (mins + SLOT_MIN - FIRST_MIN) / 375.0))
            for s, P in prev_row.items():
                if s in open_pos:
                    continue
                a = day_arrays[s][day]
                c = a["c"][k]
                if not np.isfinite(c) or a["cumv"][k] <= 0:
                    continue
                q = {"ltp": float(c), "atr": P["atr"], "rsi": P["rsi"], "adx": P["adx"], "ema9": P["ema9"], "ema20": P["ema20"], "ema50": P["ema50"],
                     "vwap": float(a["vwap"][k]), "pdh": P["pdh"], "pdl": P["pdl"],
                     "rvol": round((float(a["cumv"][k]) / frac) / P["vavg20"], 2) if P["vavg20"] > 0 else 1.2}
                cand_list.append((int(score_setup_from_indicators(q, desk=desk_up)["conviction"]), s, q))
            cand_list.sort(key=lambda x: (-x[0], x[1]))
            queued: List[Dict[str, Any]] = []
            for conv, s, q in cand_list:
                if len(queued) >= max_entries_per_cycle or conv < MIN_CONVICTION:
                    break
                deployed = sum(p["cash"] for p in open_pos.values()) + sum(p["cash_req"] for p in queued)
                sig = _evaluate_candidate(s, meta.get(s, {"tier": "MID", "sector": "GENERAL", "beta": 1.0}), q, cfg, desk_up, eff_cap, deployed,
                                          len(open_pos) + len(queued), _sector_counts(open_pos, [{"sector": x["sector"]} for x in queued]), cfg.risk_per_trade_pct)
                if sig:
                    sig["risk_pct"] = cfg.risk_per_trade_pct
                    queued.append(sig)
            pending = queued
        for s in list(open_pos.keys()):                            # safety net: never carry overnight
            close_pos(s, "EOD_SQUAREOFF", px_at(s, N_SLOTS - 1), N_SLOTS - 1)
        eq_dates.append(day)
        eq_vals.append(cap0 + realized_cum)

    equity = pd.Series(eq_vals, index=pd.DatetimeIndex(eq_dates))
    metrics = compute_metrics(trades, equity, cap0)
    metrics["protections"] = prot
    metrics["trading_days"] = len(days)
    return {"engine": "intraday_5m", "desk": cfg.label, "capital": cap0, "settings": {"risk_per_trade_pct": cfg.risk_per_trade_pct, "max_pos_pct": cfg.max_pos_pct,
            "baseline_slots": cfg.baseline_slots, "trail_be_pct": cfg.trail_be_pct, "trail_step_pct": cfg.trail_step_pct}, "metrics": metrics, "trades": trades,
            "equity": [{"date": str(d.date()), "equity": round(float(v), 2)} for d, v in equity.items()],
            "caveats": CAVEATS_COMMON + [f"Only {len(days)} trading days of 5-minute history are available from Yahoo (~60 calendar days): treat results as a smoke test, not proof.",
                                         "Daily indicators use completed days only (live also blends the partial current day)."]}


# ----------------------------------------------------------------------------- data loading (needs internet)
def _split_download(raw: Any, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    for s in symbols:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
            if len(sub):
                out[s] = sub
        except Exception:
            continue
    return out


def download_history(symbols: List[str], period: str = "5y", interval: str = "1d", chunk: int = 20) -> Dict[str, pd.DataFrame]:
    import yfinance as yf
    out: Dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        raw = yf.download(tickers=part, period=period, interval=interval, group_by="ticker", auto_adjust=True, progress=False, threads=False)
        out.update(_split_download(raw, part))
    return out


def universe_meta(desk_key: str) -> Dict[str, Dict[str, Any]]:
    from core.intelligence import EQUITY_UNIVERSE
    d = desk_key.upper()
    return {m["symbol"]: {"tier": m["tier"], "sector": m["sector"], "beta": m["beta"]} for m in EQUITY_UNIVERSE if d in m["desks"]}


def run_swing_backtest_from_yahoo(years: int = 3, overrides: Optional[Dict[str, Any]] = None, max_entries_per_day: int = 3) -> Dict[str, Any]:
    meta = universe_meta("SWING")
    period = f"{years + 1}y"                       # +1 year of indicator warm-up
    data = download_history(list(meta.keys()), period=period, interval="1d")
    idx = download_history(["^NSEI"], period=period, interval="1d").get("^NSEI")
    vix = download_history(["^INDIAVIX"], period=period, interval="1d").get("^INDIAVIX")
    start = None
    if idx is not None and len(idx):
        start = str((_clean_ohlcv(idx).index[-1] - pd.DateOffset(years=years)).date())
    return run_daily_backtest(data, meta, idx, vix, desk="swing", overrides=overrides, start=start, max_entries_per_day=max_entries_per_day)


def run_intraday_backtest_from_yahoo(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = universe_meta("INTRADAY")
    syms = list(meta.keys())
    intr = download_history(syms, period="59d", interval="5m")
    dly = download_history(syms, period="2y", interval="1d")
    return run_intraday_backtest(intr, dly, meta, desk="intraday", overrides=overrides)
