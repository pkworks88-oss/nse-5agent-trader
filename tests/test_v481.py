"""Offline regression tests for the V4.8.1 patches. Run:  python tests/test_v481.py"""
import os, sys, tempfile, warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())            # local ledger goes to a throw-away dir
import numpy as np, pandas as pd

from core.charges import estimate_roundtrip_charges
from core.sizing import compute_position_sizing
from core.watchdog import evaluate_watchdog_positions
from core import market_data as md
import headless_runner as hr

def ist(h, m):  # a Wednesday, IST wall-clock -> UTC datetime
    t = h * 60 + m - 330
    return datetime(2026, 11, 25, t // 60, t % 60, tzinfo=timezone.utc)

# 1. F&O charges no longer fall into the commodity branch
assert estimate_roundtrip_charges(150, 180, 50, "F&O") == estimate_roundtrip_charges(150, 180, 50, "FNO") != estimate_roundtrip_charges(150, 180, 50, "Commodity")

# 2. Swing overflow: 15 baseline, +5 only for conviction >= 90
kw = dict(desk_capital=1e6, deployed_capital=0, entry_price=1000, stop_loss=980, target_price=1040, max_pos_pct=15, baseline_slots=15,
          allow_overflow_slots=True, max_overflow_slots=5)
assert compute_position_sizing(conviction_score=80, current_open_slots=15, **kw)["allocation_tag"] == "BLOCKED_SLOT_CAP"
r = compute_position_sizing(conviction_score=92, current_open_slots=15, **kw); assert r["approved"] and r["allocation_tag"] == "SLOT_OVERFLOW"
assert not compute_position_sizing(conviction_score=95, current_open_slots=20, **kw)["approved"]
assert compute_position_sizing(conviction_score=70, current_open_slots=14, **kw)["approved"]

# 3. Entry cutoff 15:00
assert md.get_nse_session_status(ist(14, 30))["can_open_new_entries"]
assert md.get_nse_session_status(ist(14, 59))["can_open_new_entries"]
assert not md.get_nse_session_status(ist(15, 0))["can_open_new_entries"]

# 4. Trailing stop: no look-ahead stop-out; 25% breakeven then 10% steps
base = {"symbol": "X.NS", "desk": "Swing", "side": "LONG", "entry": 100, "sl": 95, "initial_sl": 95, "target": 110, "qty": 10, "status": "OPEN", "timestamp": "t"}
res = evaluate_watchdog_positions([dict(base)], {"X.NS": {"ltp": 105, "bar_high": 105.2, "bar_low": 99.0}}, dry_run=False, now_utc=ist(11, 0))
assert [a["action"] for a in res["actions"]] == ["TRAIL_RATCHET"], res["actions"]        # ratchets, does NOT exit on the old wick
sl1 = res["trades"][0]["sl"]; assert 100 < sl1 < 105
res2 = evaluate_watchdog_positions([dict(res["trades"][0])], {"X.NS": {"ltp": 99, "bar_high": 105, "bar_low": 98}}, dry_run=False, now_utc=ist(11, 1))
assert res2["actions"][0]["status"] == "TRAILING_SL_HIT"                                   # next tick the raised stop is honoured
r25 = evaluate_watchdog_positions([dict(base)], {"X.NS": {"ltp": 102.4, "bar_high": 102.4, "bar_low": 102.4}}, dry_run=False, now_utc=ist(11, 0))
assert not r25["actions"]                                                                    # 24% progress: nothing yet
r26 = evaluate_watchdog_positions([dict(base)], {"X.NS": {"ltp": 102.6, "bar_high": 102.6, "bar_low": 102.6}}, dry_run=False, now_utc=ist(11, 0))
assert r26["actions"][0]["action"] == "TRAIL_RATCHET" and 100 < r26["trades"][0]["sl"] < 101   # 26% -> breakeven + charges

# 5. Profit floor: peak only counts on the day it was set
today = hr._today_ist_date_str()
assert hr._effective_peak({"combined_peak_pnl": 4200, "peak_date": today}) == 4200
assert hr._effective_peak({"combined_peak_pnl": 4200, "peak_date": "2000-01-01"}) == 0.0
assert hr._effective_peak({"combined_peak_pnl": 4200}) == 0.0

# 6. Batch quotes use REAL indicators (mock yfinance)
rng = np.random.default_rng(1)
def daily(start, drift):
    n = 250; close = start * np.cumprod(1 + rng.normal(drift, 0.01, n))
    idx = pd.bdate_range(end="2026-11-24", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": rng.integers(1e6, 2e6, n)}, index=idx)
def minute(px):
    idx = pd.date_range("2026-11-24 09:15", periods=30, freq="min")
    return pd.DataFrame({"Open": px, "High": px * 1.001, "Low": px * 0.999, "Close": px, "Volume": 1000}, index=idx)
_DAILY = {}
class FakeYF:
    @staticmethod
    def download(tickers, period, interval, **kw):
        frames = {}
        for t in tickers:
            d = _DAILY.setdefault(t, daily(1000 if not t.startswith("^") else 25000, 0.001))   # deterministic per ticker
            if interval == "1d":
                frames[t] = d
            else:   # 1m price: indices are 2% below prev close (falling market), stocks flat
                prev = float(d["Close"].iloc[-2])
                frames[t] = minute(prev * 0.98 if t.startswith("^") else float(d["Close"].iloc[-1]))
        return pd.concat(frames, axis=1)
    class Ticker:
        def __init__(self, s): pass
        calendar = {}
md.yf = FakeYF; md._YF_RATE_LIMITED_UNTIL = 0.0; md._QUOTE_CACHE.clear(); md._HISTORY_CACHE.clear()
q = md.get_live_quotes_batch(["RELIANCE.NS", "^NSEI"], force_live=True)
assert q["RELIANCE.NS"]["feed"] == "yfinance_batch_1m"
assert q["RELIANCE.NS"]["atr"] != md.FALLBACK_QUOTES["RELIANCE.NS"]["atr"] and "ema20" in q["RELIANCE.NS"]
assert q["^NSEI"]["chg_pct"] != 0.0, "index change must be real, not 0.0"
from core.intelligence import build_dynamic_fno_option_candidates
md._QUOTE_CACHE.clear()
cands = build_dynamic_fno_option_candidates()
assert cands[0]["symbol"].endswith("PE") and q["^NSEI"]["chg_pct"] < 0, (cands[0]["symbol"], q["^NSEI"]["chg_pct"])   # falling index -> PE first

# 7. No live data => no trades on fabricated prices
md.yf = None; md._QUOTE_CACHE.clear(); md._HISTORY_CACHE.clear()
out = hr.run_cycle("INTRADAY", max_entries=1, ignore_session_gate=True, cas_enabled=True)
assert out["entered"] == 0, out
tr = hr.LedgerStore().load_all()["trades"]; assert not tr, "ledger must stay empty on stale feeds"

print("ALL TESTS PASSED")
