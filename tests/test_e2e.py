"""End-to-end: run_cycle() for FNO / SWING / COMMODITY with a mocked market and a local ledger."""
import os, sys, json, tempfile, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
os.chdir(tempfile.mkdtemp())
import numpy as np, pandas as pd
from datetime import datetime, timezone
NOW = datetime(2026, 11, 25, 5, 30, tzinfo=timezone.utc)      # Wed 11:00 IST: deterministic, independent of the real clock
from core import market_data as md, intelligence as intel
import headless_runner as hr
from synth import make_daily

def idx_q(ltp, chg, ema9, ema20, adx, rsi, atr):
    return {"ltp": ltp, "chg_pct": chg, "ema9": ema9, "ema20": ema20, "adx": adx, "rsi": rsi, "atr": atr, "pdh": ltp * 1.008, "pdl": ltp * 0.992,
            "bar_high": ltp * 1.0004, "bar_low": ltp * 0.9996, "rvol": 1.1, "feed": "yfinance_batch_1m", "prev_close": ltp / (1 + chg / 100)}
MOCK = {"^NSEI": idx_q(25000, -0.8, 24900, 25100, 26, 40, 250), "^NSEBANK": idx_q(55000, -0.9, 54800, 55300, 27, 38, 600),
        "^INDIAVIX": {"ltp": 14.0, "chg_pct": 0.0, "feed": "yfinance_batch_1m"}}
_orig = md.get_live_quotes_batch
def fake_batch(symbols, default_entries=None, force_live=False):
    if symbols and all(s in MOCK for s in symbols):
        return {s: dict(MOCK[s]) for s in symbols}
    return _orig(symbols, default_entries, force_live)
for mod in (md, intel, hr):
    mod.get_live_quotes_batch = fake_batch
md.is_expiry_day = lambda *a, **k: False
md.yf = object()

# ---------------- F&O ----------------
out = hr.run_cycle("FNO", max_entries=1, ignore_session_gate=True, cas_enabled=True)
assert out["entered"] == 1, out
st = hr.LedgerStore().load_all()
t = [x for x in st["trades"] if x["status"] == "OPEN"][0]
assert t["symbol"].startswith("NIFTY") and t["symbol"].endswith("PE") and t["desk"] == "F&O", t
assert t["qty"] == 65 and abs(t["initial_target"] - t["target"]) < 1e-9
cash_after = st["accounts"]["fno_cash"]; assert abs((100000.0 - cash_after) - t["entry"] * 65) < 1.0, (cash_after, t["entry"] * 65)
print("F&O entered:", t["symbol"], "qty", t["qty"], "entry", t["entry"], "sl", t["sl"], "target", t["target"])
# index keeps falling -> PE premium rises -> trade ratchets / stays open; then index rips -> stop
MOCK["^NSEI"] = idx_q(24950, -0.2, 24900, 25100, 28, 38, 250)
md._QUOTE_CACHE.clear()
wd = hr.run_watchdog_pass(hr.LedgerStore(), hr.LedgerStore().load_all(), dry_run=False, now_utc=NOW)
t2 = [x for x in hr.LedgerStore().load_all()["trades"] if x["symbol"] == t["symbol"]][0]
assert t2["status"] == "OPEN" and t2["sl"] >= t["sl"], t2
MOCK["^NSEI"] = idx_q(25400, 1.2, 25200, 25100, 24, 62, 250)
md._QUOTE_CACHE.clear()
hr.run_watchdog_pass(hr.LedgerStore(), hr.LedgerStore().load_all(), dry_run=False, now_utc=NOW)
t3 = [x for x in hr.LedgerStore().load_all()["trades"] if x["symbol"] == t["symbol"]][0]
assert t3["status"] in ("SL_HIT", "TRAILING_SL_HIT"), t3
print("F&O closed:", t3["status"], "net", t3["pnl"])
# -1,500 breaker: realized loss today blocks further F&O entries
MOCK["^NSEI"] = idx_q(25000, -0.8, 24900, 25100, 26, 40, 250)
md._QUOTE_CACHE.clear()
out2 = hr.run_cycle("FNO", max_entries=1, ignore_session_gate=True, cas_enabled=True)
print("second F&O cycle:", out2.get("gate_blocked") or f"entered={out2['entered']}")

# ---------------- SWING / COMMODITY (offline stocks: stale feed must block everything) ----------------
md.yf = None
for desk in ("SWING", "COMMODITY"):
    o = hr.run_cycle(desk, max_entries=1, ignore_session_gate=True, cas_enabled=True)
    assert o["entered"] == 0, (desk, o)
    print(desk, "with no live data ->", o.get("gate_blocked") or "0 entries (stale-feed rejection)")
print("E2E PASSED")
