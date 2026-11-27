"""Chart data must never raise, for every kind of symbol the desk pages request."""
import os, sys, warnings, tempfile
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); os.chdir(tempfile.mkdtemp())
import numpy as np, pandas as pd
import core.market_data as md

def check(bars, n):
    assert len(bars) == n and all(set(b) >= {"bar", "open", "high", "low", "close", "vol"} for b in bars)

md.yf = None                                           # offline: synthetic fallback must still work
for sym in ("RELIANCE.NS", "NIFTY 24850 CE", "BANKNIFTY 53000 PE", "GOLDM MCX", "GOLDPETAL MCX", "CRUDEOILM MCX", "SOMETHING ELSE"):
    check(md.get_ohlcv_bars(sym, 1000.0, count=32), 32)

def fake(sym, period="1y", interval="1d"):
    rng = np.random.default_rng(3); base = {"GC=F": 4000.0, "CL=F": 70.0, "USDINR=X": 88.0}.get(sym, 1000.0)
    n = 120; c = base * np.cumprod(1 + rng.normal(0, 0.01, n)); idx = pd.bdate_range(end="2026-11-20", periods=n)
    return pd.DataFrame({"Open": c, "High": c * 1.004, "Low": c * 0.996, "Close": c, "Volume": 1e5}, index=idx)
md.yf = object(); md._fetch_real_ohlcv_df = fake
for sym in ("GOLDPETAL MCX", "CRUDEOILM MCX", "GOLDM MCX", "RELIANCE.NS"):
    bars = md.get_ohlcv_bars(sym, 1000.0, count=32); check(bars, 32)
    assert bars[0]["bar"] != "B1", f"{sym}: expected real dated candles, got the synthetic placeholder"
g = md.get_ohlcv_bars("GOLDPETAL MCX", 1.0)[-1]["close"]; assert 5000 < g < 30000, g       # INR per gram
print("CHART TESTS PASSED")

# ============================ selectable ranges / candle sizes ============================
import itertools
SESS = {"1d": 1, "5d": 5, "1mo": 21, "3mo": 63}
def fake_any(sym, period="1y", interval="1d"):
    rng = np.random.default_rng(abs(hash((sym, period, interval))) % 10000)
    base = {"GC=F": 4000.0, "CL=F": 70.0, "USDINR=X": 88.0}.get(sym, 25000.0 if sym.startswith("^") else 1000.0)
    if interval in ("1m", "5m", "15m", "30m", "1h"):
        m = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[interval]
        days = pd.bdate_range(end="2026-11-25", periods=SESS.get(period, 1))
        idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=15 + k * m) for d in days for k in range(int(375 // m))]).tz_localize("Asia/Kolkata")
    else:
        n = {"1mo": 22, "3mo": 63, "6mo": 126, "1y": 252, "5y": 1300}.get(period, 100)
        idx = pd.bdate_range(end="2026-11-25", periods=n if interval == "1d" else (n if interval != "1wk" else max(2, n // 5)))
    c = base * np.cumprod(1 + rng.normal(0, 0.002, len(idx)))
    return pd.DataFrame({"Open": c, "High": c * 1.001, "Low": c * 0.999, "Close": c, "Volume": 1e4}, index=idx)
md._fetch_real_ohlcv_df = fake_any; md.yf = object()

def bars(sym, rk, iv=None): return md.get_chart_bars(sym, 1000.0, rk, iv)
r = bars("RELIANCE.NS", "1H");            assert r["source"] == "live" and len(r["bars"]) <= 60 and ":" in r["bars"][0]["bar"] and " " not in r["bars"][0]["bar"], r["bars"][0]
r = bars("RELIANCE.NS", "1H", "5m");      assert len(r["bars"]) == 12
r = bars("RELIANCE.NS", "1D");            assert 70 <= len(r["bars"]) <= 76 and r["bars"][0]["bar"] == "09:15" and r["interval"] == "5m"
r = bars("RELIANCE.NS", "5D");            assert 120 <= len(r["bars"]) <= 130 and " " in r["bars"][0]["bar"] and r["interval"] == "15m"
r = bars("RELIANCE.NS", "1M");            assert 20 <= len(r["bars"]) <= 23 and r["interval"] == "1d"
r = bars("RELIANCE.NS", "1M", "1h");      assert len(r["bars"]) > 100 and ":" in r["bars"][0]["bar"]
r = bars("RELIANCE.NS", "3M");            assert 60 <= len(r["bars"]) <= 64
r = bars("RELIANCE.NS", "1Y");            assert len(r["bars"]) > 240 and "2026" in r["bars"][-1]["bar"] or "2025" in r["bars"][-1]["bar"]
r = bars("RELIANCE.NS", "5Y");            assert r["interval"] == "1wk" and len(r["bars"]) > 200
assert bars("RELIANCE.NS", "1D", "1d")["interval"] == "5m", "an interval not allowed for the range must fall back to its default"
assert bars("RELIANCE.NS", "nonsense")["range"] == "1M"
o = bars("NIFTY 24850 CE", "1D");          assert o["source"] == "underlying" and "underlying index" in o["note"] and o["bars"][0]["close"] > 5000
g = bars("GOLDPETAL MCX", "1D");           assert g["source"] == "proxy" and 5000 < g["bars"][-1]["close"] < 30000 and ":" in g["bars"][0]["bar"], g["bars"][-1]
c = bars("CRUDEOILM MCX", "5D", "30m");    assert c["source"] == "proxy" and 2000 < c["bars"][-1]["close"] < 12000
gm = bars("GOLDPETAL MCX", "1Y");          assert gm["source"] == "proxy" and len(gm["bars"]) > 200
# every range x every allowed candle size x every symbol type: no exception, never empty
for sym in ("RELIANCE.NS", "NIFTY 24850 CE", "GOLDPETAL MCX", "CRUDEOILM MCX"):
    for rk, spec in md.CHART_RANGES.items():
        for iv in spec["intervals"]:
            out = bars(sym, rk, iv); assert len(out["bars"]) >= 2 and out["source"] != "placeholder", (sym, rk, iv)
md._fetch_real_ohlcv_df = lambda *a, **k: None
assert bars("RELIANCE.NS", "1D")["source"] == "placeholder" and bars("GOLDPETAL MCX", "1M")["source"] == "placeholder"
print("RANGE TESTS PASSED")
