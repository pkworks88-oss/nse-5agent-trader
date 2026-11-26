"""V4.8.2 tests: F&O (lots + Black-Scholes), commodity proxies, breaker liquidation, swing extras, saved settings."""
import os, sys, json, tempfile, warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
os.chdir(tempfile.mkdtemp())
import numpy as np, pandas as pd

from core import market_data as md, intelligence as intel
from core.config import get_effective_desk_config, FNO_LOT_SIZES
from core.sizing import compute_position_sizing
from core.watchdog import evaluate_watchdog_positions
import headless_runner as hr

def utc_for_ist(y, mo, d, h, m):
    t = h * 60 + m - 330
    return datetime(y, mo, d, t // 60, t % 60, tzinfo=timezone.utc)

# ---------- mock market ----------
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
md.yf = object()                                   # "yfinance present"; real network is never touched below

# ---------- 1. F&O: Black-Scholes premium, real lots, direction ----------
q_ce = md._estimate_index_option_quote("NIFTY 25000 CE"); q_pe = md._estimate_index_option_quote("NIFTY 25000 PE")
assert q_ce["feed"] == "index_option_bs_model" and 30 < q_ce["ltp"] < 400, q_ce
assert q_pe["bar_high"] >= q_pe["ltp"] >= q_pe["bar_low"] and q_ce["bar_high"] >= q_ce["ltp"] >= q_ce["bar_low"]
MOCK["^NSEI"]["ltp"] = 24900
assert md._estimate_index_option_quote("NIFTY 25000 PE")["ltp"] > q_pe["ltp"], "PE premium must rise when spot falls"
assert md._estimate_index_option_quote("NIFTY 25000 CE")["ltp"] < q_ce["ltp"]
MOCK["^NSEI"]["ltp"] = 25000
cands = intel.build_dynamic_fno_option_candidates()
nifty = next(c for c in cands if c["symbol"].startswith("NIFTY")); bank = next(c for c in cands if c["symbol"].startswith("BANKNIFTY"))
assert nifty["symbol"].endswith("PE") and bank["symbol"].endswith("PE"), (nifty["symbol"], bank["symbol"])   # falling, trending index -> puts
assert nifty["lot_size"] == FNO_LOT_SIZES["NIFTY"] == 65 and bank["lot_size"] == 30 and nifty["conviction"] >= 65
cfg = get_effective_desk_config("fno")
def size(c):
    return compute_position_sizing(desk_capital=cfg.capital, deployed_capital=0, entry_price=c["entry"], stop_loss=c["sl"], target_price=c["target"], conviction_score=c["conviction"],
                                   max_pos_pct=cfg.max_pos_pct, risk_per_trade_pct=cfg.risk_per_trade_pct, soft_cushion_pct=cfg.soft_cushion_pct, baseline_slots=cfg.baseline_slots,
                                   current_open_slots=0, lot_size=c["lot_size"], atr=c["atr"], beta=c["beta"], margin_pct=cfg.margin_pct)
sn = size(nifty); assert sn["approved"] and sn["qty"] == 65, sn                     # exactly 1 lot, risk <= Rs2,500
assert (nifty["entry"] - nifty["sl"]) * sn["qty"] <= cfg.capital * cfg.risk_per_trade_pct / 100 + 1
sb = size(bank); assert not sb["approved"], "BankNifty monthly ATM lot is too big for Rs1L at 2.5% risk - must be blocked, not mis-sized"
MOCK["^NSEI"] = idx_q(25000, 0.0, 25000, 25000, 12, 50, 250)                          # chop -> below conviction gate
chop = [c for c in intel.build_dynamic_fno_option_candidates() if c["symbol"].startswith("NIFTY")][0]
assert chop["conviction"] < 65
MOCK["^NSEI"] = idx_q(25000, -0.8, 24900, 25100, 26, 40, 250)

# ---------- 2. Commodity: live proxy + margin sizing ----------
def fake_ohlcv(sym, period="1y", interval="1d"):
    rng = np.random.default_rng(7 if sym == "GC=F" else 8)
    base = {"GC=F": 4000.0, "CL=F": 70.0, "USDINR=X": 88.0}[sym]
    n = 250 if interval == "1d" else 30
    c = base * np.cumprod(1 + rng.normal(0, 0.012 if sym != "USDINR=X" else 0.001, n))
    idx = pd.bdate_range(end="2026-11-20", periods=n) if interval == "1d" else pd.date_range("2026-11-20 09:15", periods=n, freq="min")
    return pd.DataFrame({"Open": c, "High": c * 1.005, "Low": c * 0.995, "Close": c, "Volume": 1e5}, index=idx)
md._fetch_real_ohlcv_df = fake_ohlcv
g = md.get_live_quotes_batch(["GOLDPETAL MCX"], force_live=True)["GOLDPETAL MCX"]
cl = md.get_live_quotes_batch(["CRUDEOILM MCX"], force_live=True)["CRUDEOILM MCX"]
assert g["feed"] == cl["feed"] == "yfinance_commodity_proxy" and 5000 < g["ltp"] < 30000 and 2000 < cl["ltp"] < 12000, (g["ltp"], cl["ltp"])
ccfg = get_effective_desk_config("commodity"); assert ccfg.capital == 100000 and ccfg.margin_pct == 0.10
sg = compute_position_sizing(desk_capital=ccfg.capital, deployed_capital=0, entry_price=12000, stop_loss=11800, target_price=12400, conviction_score=80, max_pos_pct=ccfg.max_pos_pct,
                             risk_per_trade_pct=ccfg.risk_per_trade_pct, soft_cushion_pct=10, baseline_slots=2, current_open_slots=0, lot_size=1, margin_pct=ccfg.margin_pct)
assert sg["approved"] and sg["qty"] == 5 and abs(sg["cash_required"] - 6000) < 1, sg                  # 5 g, only margin blocked
sc = compute_position_sizing(desk_capital=ccfg.capital, deployed_capital=0, entry_price=5800, stop_loss=5700, target_price=6000, conviction_score=80, max_pos_pct=ccfg.max_pos_pct,
                             risk_per_trade_pct=ccfg.risk_per_trade_pct, soft_cushion_pct=10, baseline_slots=2, current_open_slots=0, lot_size=10, margin_pct=ccfg.margin_pct)
assert sc["approved"] and sc["qty"] == 10, sc                                                         # one 10-bbl lot
md._fetch_real_ohlcv_df = _orig_fetch = None
del md._fetch_real_ohlcv_df  # restore: re-import original
import importlib; importlib.reload(md)

# ---------- 3. Watchdog: breaker liquidation, swing floor, rollover, time stop ----------
WED = utc_for_ist(2026, 11, 25, 11, 0)
def tr(desk, sym, entry, sl, target, qty, ts="2026-11-25 10:00:00 IST", **kw):
    d = {"symbol": sym, "desk": desk, "side": "LONG", "entry": entry, "sl": sl, "initial_sl": sl, "target": target, "qty": qty, "status": "OPEN", "timestamp": ts}; d.update(kw); return d
# 3a. intraday breaker: realized -1,400 + open loss -> liquidate INTRADAY only; swing book untouched
trades = [tr("Intraday", "A.NS", 1000, 900, 1200, 50), tr("Swing", "B.NS", 1000, 900, 1300, 100)]
quotes = {"A.NS": {"ltp": 995, "bar_high": 996, "bar_low": 994}, "B.NS": {"ltp": 940, "bar_high": 941, "bar_low": 939}}
r = evaluate_watchdog_positions(trades, quotes, dry_run=False, now_utc=WED, today_realized_by_desk={"intraday": -1400.0})
st = {a["symbol"]: a["status"] for a in r["actions"] if a["action"] == "EXIT"}
assert st == {"A.NS": "CIRCUIT_BREAKER_SQUAREOFF"}, st
assert r["breaker_tripped"] == ["intraday"]
# 3b. swing basket floor: peak 8k locks 3.5k; basket 3k -> liquidate; disabled by setting
sw = [tr("Swing", "C.NS", 100, 90, 140, 1000, ts="2026-11-20 10:00:00 IST")]
q3 = {"C.NS": {"ltp": 103.2, "bar_high": 103.3, "bar_low": 103.1}}
r = evaluate_watchdog_positions(sw, q3, dry_run=False, now_utc=WED, swing_peak_pnl=8000.0)
assert any(a["status"] == "PROFIT_FLOOR_SQUAREOFF" for a in r["actions"]), r["actions"]
off = get_effective_desk_config("swing"); from dataclasses import replace; off = replace(off, basket_floor=False)
r = evaluate_watchdog_positions([dict(sw[0])], q3, dry_run=False, now_utc=WED, swing_peak_pnl=8000.0, desk_configs={"swing": off})
assert not any(a["status"] == "PROFIT_FLOOR_SQUAREOFF" for a in r["actions"])
# 3c. target rollover then T2 exit
t1 = [tr("Swing", "D.NS", 100, 95, 110, 100, ts="2026-11-20 10:00:00 IST")]
r = evaluate_watchdog_positions(t1, {"D.NS": {"ltp": 110.2, "bar_high": 110.5, "bar_low": 109.9}}, dry_run=False, now_utc=WED)
assert [a["action"] for a in r["actions"]] == ["TARGET_ROLLOVER"] and r["trades"][0]["status"] == "OPEN"
rt = r["trades"][0]; assert rt["target"] == 115.0 and 109.5 < rt["sl"] < 110, rt
r2 = evaluate_watchdog_positions([rt], {"D.NS": {"ltp": 115.5, "bar_high": 115.6, "bar_low": 115.2}}, dry_run=False, now_utc=WED)
assert r2["actions"][0]["status"] == "TARGET_HIT" and r2["trades"][0]["pnl"] > 1400
# 3d. time stop (only in EOD window, only if unprotected)
EOD = utc_for_ist(2026, 11, 25, 15, 20)
old = tr("Swing", "E.NS", 100, 95, 120, 100, ts="2026-11-13 10:00:00 IST")
flat = {"E.NS": {"ltp": 100.5, "bar_high": 100.6, "bar_low": 100.4}}
assert evaluate_watchdog_positions([dict(old)], flat, dry_run=False, now_utc=EOD)["actions"][0]["status"] == "TIME_STOP"
assert not evaluate_watchdog_positions([dict(old)], flat, dry_run=False, now_utc=WED)["actions"], "no time stop intraday"
prot = dict(old); prot["sl"] = 100.2   # stop already above entry (profit locked), below price
assert not [a for a in evaluate_watchdog_positions([prot], flat, dry_run=False, now_utc=EOD)["actions"] if a["action"] == "EXIT"], "profit-protected trade must not be time-stopped"
young = tr("Swing", "E.NS", 100, 95, 120, 100, ts="2026-11-23 10:00:00 IST")
assert not evaluate_watchdog_positions([young], flat, dry_run=False, now_utc=EOD)["actions"]

# ---------- 4. Regime ----------
from core.quant_engine import classify_swing_regime
assert classify_swing_regime(25000, 24800, 0.2, 15)["mode"] == "NORMAL"
assert classify_swing_regime(24700, 24800, 0.2, 15)["defensive"] and classify_swing_regime(25000, 24800, -0.9, 15)["defensive"] and classify_swing_regime(25000, 24800, 0.1, 25)["defensive"]
md2 = md  # reloaded module: patch again
md2.get_live_quotes_batch = fake_batch
md2._fetch_real_ohlcv_df = lambda s, period="3mo", interval="1d": pd.DataFrame({"Close": np.linspace(24000, 24900, 60)})
MOCK["^NSEI"]["chg_pct"] = -0.9
assert md2.get_swing_regime()["mode"] == "DEFENSIVE"
MOCK["^NSEI"]["chg_pct"] = 0.3
assert md2.get_swing_regime()["mode"] == "NORMAL"
MOCK["^NSEI"]["feed"] = "fallback_calibrated"
assert md2.get_swing_regime()["mode"] == "UNKNOWN"
MOCK["^NSEI"]["feed"] = "yfinance_batch_1m"

# ---------- 5. Dashboard settings reach the runner ----------
store = hr.LedgerStore()
store.update_account_state({"desk_settings": json.dumps({"fno": {"capital": 250000, "risk_per_trade_pct": 2.5}, "swing": {"baseline_slots": 20}})})
acc = hr.LedgerStore().load_all()["accounts"]
e = get_effective_desk_config("F&O", acc)
assert e.capital == 250000 and e.risk_per_trade_pct == 2.5 and get_effective_desk_config("swing", acc).baseline_slots == 20
assert get_effective_desk_config("intraday", acc).capital == 100000                     # untouched desks keep defaults

# ---------- 6. Swing day-P&L is a DAILY number ----------
today = hr._today_ist_date_str()
open_sw = [{"symbol": "F.NS", "entry": 100.0, "qty": 100, "timestamp": "2026-11-10 10:00:00 IST"}, {"symbol": "G.NS", "entry": 50.0, "qty": 100, "timestamp": f"{today} 10:00:00 IST"}]
hr.get_live_quotes_batch = lambda syms, **k: {"F.NS": {"ltp": 90.0, "prev_close": 89.0, "feed": "yfinance_batch_1m"}, "G.NS": {"ltp": 51.0, "prev_close": 49.0, "feed": "yfinance_batch_1m"}}
assert hr._swing_day_pnl([], open_sw) == (90 - 89) * 100 + (51 - 50) * 100, "old trade: vs prev close (not -1000 since entry); new trade: vs entry"
print("V4.8.2 TESTS PASSED")
