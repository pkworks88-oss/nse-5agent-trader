"""Scheduler behaviour: watchdog keeps its 20s-style clock while a slow cycle runs; cycles fire on schedule; one failing desk doesn't stop others."""
import os, sys, time, threading, tempfile, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp())
import headless_runner as hr, run_local_loop as rl

# pure schedule maths
last = {}
base = 1_700_000_100.0                          # arbitrary
assert rl.due_jobs(base, last) == ["5min", "15min"]            # first call: everything is due once
assert rl.due_jobs(base + 10, last) == []
nxt5 = (int(base // 300) + 1) * 300
assert rl.due_jobs(nxt5 + 1, last) == ["5min"]
nxt15 = (int(base // 900) + 1) * 900
assert "15min" in rl.due_jobs(nxt15 + 1, last)
# IST marks: epoch-aligned slots coincide with IST :00/:05/:15
import datetime as dt
t = dt.datetime(2026, 11, 25, 10, 15, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp()
assert t % 900 == 0 and t % 300 == 0

# live scheduler with fakes (intervals shrunk 100x: watchdog 0.2s, "5 min"=3s, "15 min"=6s)
events, lock = [], threading.Lock()
active_cycles = {"n": 0, "max": 0}
class FakeStore:
    status_msg = "fake"
    def load_all(self): return {"trades": [], "accounts": {}, "source": "fake"}
def fake_wd(store, state, dry_run=False, now_utc=None):
    with lock: events.append(("wd", time.monotonic()))
    return {"actions": [], "skipped_stale_feed": []}
def fake_cycle(desk="SWING", max_entries=1, ignore_session_gate=False, cas_enabled=None, dry_run=False, skip_watchdog=False):
    assert skip_watchdog is True, "scheduler already runs the watchdog; cycles must not duplicate it"
    with lock:
        active_cycles["n"] += 1; active_cycles["max"] = max(active_cycles["max"], active_cycles["n"]); events.append((desk, time.monotonic()))
    try:
        time.sleep(1.2)                                  # a SLOW desk (1.2s vs the 0.2s watchdog interval)
        if desk == "FNO": raise RuntimeError("boom")    # one desk fails
        return {"evaluated": 3, "entered": 0}
    finally:
        with lock: active_cycles["n"] -= 1
hr.LedgerStore = lambda: FakeStore(); hr.run_watchdog_pass = fake_wd; hr.run_cycle = fake_cycle
st = rl.run_scheduler(force_session=True, wd_interval=0.2, five_sec=3, fifteen_sec=6, max_runtime=7.5)
wd = [e[1] for e in events if e[0] == "wd"]
assert len(wd) >= 25, f"watchdog starved by slow cycles: only {len(wd)} ticks in 7.5s"
gaps = [b - a for a, b in zip(wd, wd[1:])]
assert max(gaps) < 0.6, f"watchdog gap too large while cycles run: {max(gaps):.2f}s"
by = lambda d: [e[1] for e in events if e[0] == d]
assert len(by("INTRADAY")) >= 2 and len(by("COMMODITY")) >= 2, "5-minute desks must keep firing"
assert len(by("SWING")) >= 1, "swing must fire"
assert st["cycle_errors"] >= 1 and len(by("COMMODITY")) >= 1, "FNO failing must not stop COMMODITY"
assert active_cycles["max"] == 1, "cycle batches must never overlap"
print(f"watchdog ticks={len(wd)} max gap={max(gaps):.2f}s | INTRADAY x{len(by('INTRADAY'))} SWING x{len(by('SWING'))} | errors survived={st['cycle_errors']}")
print("LOOP TESTS PASSED")
