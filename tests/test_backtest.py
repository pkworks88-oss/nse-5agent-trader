"""Backtester tests: indicator parity with the live code, exact exit mechanics, accounting invariants."""
import os, sys, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import numpy as np, pandas as pd
import core.backtest_engine as be
from synth import make_daily, make_universe

# ---- 1. vectorised indicators == live analyze_ohlcv_indicators ----
for seed in (1, 2, 3):
    diffs = be.parity_check(make_daily(400, seed=seed))
    assert max(diffs.values()) < 0.02, diffs

# ---- 2. exact mechanics on hand-built bars ----
SIG = 100.37                                   # unique close that triggers the (patched) signal
def scenario(post_bars, n_flat=120):
    """flat history, one signal bar (close=SIG), then custom (open, high, low, close) bars. Returns trades."""
    idx = pd.bdate_range("2025-01-01", periods=n_flat + 1 + len(post_bars))
    hist = [(100.1, 100.5, 99.6, 100.0 if k % 2 == 0 else 100.2) for k in range(n_flat)]     # choppy => RSI ~50, not 100
    rows = hist + [(100.1, 101, 99.9, SIG)] + list(post_bars)
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx); df["Volume"] = 1e6
    orig = be.score_setup_from_indicators
    be.score_setup_from_indicators = lambda q, desk="SWING": {"entry": q["ltp"], "sl": round(q["ltp"] * 0.95, 2), "target": round(q["ltp"] * 1.10, 2),
                                                               "conviction": 90 if abs(q["ltp"] - SIG) < 1e-9 else 40, "rationale": "test"}
    try:
        r = be.run_daily_backtest({"T.NS": df}, {"T.NS": {"tier": "LOW", "sector": "IT", "beta": 1.0}}, None, None, desk="swing", warmup=60)
    finally:
        be.score_setup_from_indicators = orig
    return r["trades"], r

# 2a. gap below the stop fills at the OPEN, not at the stop
t, _ = scenario([(100.4, 100.6, 100.2, 100.5), (90, 91, 89, 90)])
assert len(t) == 1 and t[0]["status"] == "SL_HIT" and t[0]["exit"] == 90.0 and t[0]["entry"] == 100.4, t
# 2b. entry is the NEXT bar's open (never the signal bar's close)
assert t[0]["entry"] != SIG
# 2c. stop and target both inside one bar -> stop first (pessimistic)
t, _ = scenario([(100.4, 100.6, 100.2, 100.5), (100.5, 115, 90, 100)])
assert t[0]["status"] == "SL_HIT" and t[0]["exit"] == round(SIG * 0.95, 2), t
# 2d. Target-1 rollover (swing): no exit at T1; later exits at the rolled stop with ~T1 locked
t, _ = scenario([(100.4, 100.6, 100.2, 100.5),                 # fill @100.4
                 (101, 111.5, 100.9, 111.0),                    # touches T1 (110.41): roll -> target ~115.9, SL ~110.14
                 (111, 112, 111, 111.5),
                 (111, 111.2, 105, 106)])                       # drops through the rolled stop
assert len(t) == 1 and t[0]["rolled"] and t[0]["status"] == "TRAILING_SL_HIT", t
assert t[0]["exit"] > 108.0 and t[0]["pnl"] > 0, t
# 2e. dead-money time stop after 7 bars with no progress
t, _ = scenario([(100.4, 100.6, 100.2, 100.5)] + [(100.5, 100.9, 100.1, 100.5)] * 9)
assert t[0]["status"] == "TIME_STOP" and t[0]["bars_held"] >= 7, t

# ---- 3. accounting invariants on a full random run ----
data, meta, idx, vix = make_universe()
start = str(data["S0.NS"].index[300].date())
r = be.run_daily_backtest(data, meta, idx, vix, desk="swing", start=start)
tr = r["trades"]; assert len(tr) > 50
assert abs(sum(x["pnl"] for x in tr) - (r["equity"][-1]["equity"] - r["capital"])) < 1.0, "equity must equal capital + net P&L"
assert all(x["exit_ts"] >= x["entry_ts"] for x in tr)
assert all(x["qty"] > 0 for x in tr)
assert all(x["bars_held"] >= 7 for x in tr if x["status"] == "TIME_STOP")
# concurrent positions never exceed baseline + overflow
cfg = be.get_desk_config("swing"); limit = cfg.baseline_slots + cfg.max_overflow_slots
ev = sorted([(x["entry_ts"], 1) for x in tr] + [(x["exit_ts"], -1) for x in tr], key=lambda e: (e[0], e[1]))
cur = peak = 0
for _, d in ev:
    cur += d; peak = max(peak, cur)
assert peak <= limit, (peak, limit)
# determinism
r2 = be.run_daily_backtest(data, meta, idx, vix, desk="swing", start=start)
assert r2["metrics"]["net_pnl"] == r["metrics"]["net_pnl"]
# basket-floor switch changes behaviour (A/B tooling works)
r3 = be.run_daily_backtest(data, meta, idx, vix, desk="swing", start=start, overrides={"basket_floor": False})
assert "PROFIT_FLOOR_SQUAREOFF" not in r3["metrics"]["exit_reasons"]

# ---- 4. intraday engine on synthetic 5-minute data ----
def make_5m(daily_df, days=25, seed=5):
    rng = np.random.default_rng(seed); frames = []
    last_days = daily_df.index[-days:]
    for d in last_days:
        times = pd.date_range(f"{d.date()} 09:15", periods=75, freq="5min")
        base = float(daily_df.loc[d, "Open"]); steps = rng.normal(0.0001, 0.0015, 75); c = base * np.cumprod(1 + steps)
        o = np.concatenate([[base], c[:-1]]); h = np.maximum(o, c) * 1.0008; l = np.minimum(o, c) * 0.9992
        frames.append(pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": rng.integers(2e4, 9e4, 75).astype(float)}, index=times))
    return pd.concat(frames)
intr = {s: make_5m(d) for s, d in list(data.items())[:8]}
ri = be.run_intraday_backtest(intr, {s: data[s] for s in intr}, meta, desk="intraday")
mi = ri["metrics"]
assert all(x["status"] != "OPEN" for x in ri["trades"])
assert abs(sum(x["pnl"] for x in ri["trades"]) - (ri["equity"][-1]["equity"] - ri["capital"])) < 1.0
assert all(x["status"] in ("SL_HIT", "TRAILING_SL_HIT", "TARGET_HIT", "EOD_SQUAREOFF", "CIRCUIT_BREAKER_SQUAREOFF", "PROFIT_FLOOR_SQUAREOFF") for x in ri["trades"])
print("intraday trades:", mi["trades"], "exits:", mi["exit_reasons"])
print("BACKTEST TESTS PASSED")
