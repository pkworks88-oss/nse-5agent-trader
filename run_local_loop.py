"""Continuous trading scheduler - ONE process, two independent clocks.

  * 20-second watchdog   : trails stops (25% breakeven + 10% steps), exits, floors, breakers, EOD square-off.
  * 5-minute cycles      : INTRADAY, FNO, COMMODITY entry scans, aligned to :00 :05 :10 ...
  * 15-minute cycle      : SWING entry scan, aligned to :00 :15 :30 :45
The cycles run in a worker thread, so a slow cycle can never delay the watchdog.

  python run_local_loop.py                       # run until 15:35 IST (waits for 09:00 IST if started early)
  python run_local_loop.py --until-ist 12:40     # GitHub 'morning' session
  python run_local_loop.py --force-session       # ignore market hours (testing)
  python run_local_loop.py --dry-run             # evaluate only, write nothing

Works on your own PC / VPS (recommended) and inside one long GitHub Actions job (see .github/workflows/v48_cycles.yml).
"""
import argparse
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

FIVE_MIN_DESKS = ("INTRADAY", "FNO", "COMMODITY")


def ist_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def log(msg: str) -> None:
    print(f"[{ist_now().strftime('%H:%M:%S')} IST] {msg}", flush=True)


def slot_id(epoch_seconds: float, interval_seconds: int) -> int:
    """Wall-clock slot number. IST is UTC+5:30 (a multiple of 15 min), so epoch-aligned slots are IST :00/:05/:15 marks."""
    return int(epoch_seconds // interval_seconds)


def due_jobs(epoch_seconds: float, last_slot: Dict[str, int], five_sec: int = 300, fifteen_sec: int = 900) -> List[str]:
    """Which cycle jobs should fire now? Each fires once per slot, immediately when the slot begins (or when we start late)."""
    out: List[str] = []
    for name, interval in (("5min", five_sec), ("15min", fifteen_sec)):
        sid = slot_id(epoch_seconds, interval)
        if last_slot.get(name) != sid:
            last_slot[name] = sid
            out.append(name)
    return out


def parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def run_scheduler(until_min: int = 15 * 60 + 35, start_min: int = 9 * 60, force_session: bool = False, dry_run: bool = False,
                  wd_interval: float = 20.0, five_sec: int = 300, fifteen_sec: int = 900, once: bool = False,
                  max_runtime: Optional[float] = None,
                  now_fn: Callable[[], datetime] = ist_now, sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, int]:
    import headless_runner as hr
    from core.market_data import _is_trading_day

    stats = {"watchdog_ticks": 0, "watchdog_errors": 0, "cycle_batches": 0, "cycle_errors": 0}
    today = now_fn().date()
    if not force_session and not _is_trading_day(today):
        log(f"{today} is not an NSE trading day - nothing to do.")
        return stats
    while not force_session and now_fn().hour * 60 + now_fn().minute < start_min:
        sleep_fn(15)
    if now_fn().hour * 60 + now_fn().minute >= until_min and not force_session:
        log("Past the end time - nothing to do.")
        return stats

    wd_store = hr.LedgerStore()                         # one connection reused for every 20-second tick
    log(f"Scheduler started. Ledger: {wd_store.status_msg}")
    last_slot: Dict[str, int] = {}
    cycle_thread: Optional[threading.Thread] = None
    next_wd = time.monotonic()
    started = time.monotonic()

    def cycle_worker(jobs: List[str]) -> None:
        desks: List[str] = []
        if "5min" in jobs:
            desks += list(FIVE_MIN_DESKS)
        if "15min" in jobs:
            desks.append("SWING")
        for d in desks:
            t0 = time.monotonic()
            try:
                res = hr.run_cycle(desk=d, max_entries=1, ignore_session_gate=force_session, dry_run=dry_run, skip_watchdog=True)
                log(f"{d:<9} cycle {time.monotonic() - t0:5.1f}s  evaluated={res.get('evaluated', 0)} entered={res.get('entered', 0)}"
                    + (f"  gate={res['gate_blocked']}" if res.get("gate_blocked") else "") + (f"  [{res['budget_note']}]" if res.get("budget_note") else ""))
            except Exception as exc:                    # one desk failing must never stop the others or the watchdog
                stats["cycle_errors"] += 1
                log(f"{d} cycle FAILED: {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=3)
        stats["cycle_batches"] += 1

    while True:
        now = now_fn()
        if not force_session and now.hour * 60 + now.minute >= until_min:
            break
        if max_runtime is not None and time.monotonic() - started > max_runtime:
            break
        t = time.monotonic()
        if t >= next_wd:                                # ---- 20-second watchdog (own clock, never blocked by cycles)
            try:
                state = wd_store.load_all()
                res = hr.run_watchdog_pass(wd_store, state, dry_run=dry_run)
                stats["watchdog_ticks"] += 1
                for a in res.get("actions", []):
                    log(f"WATCHDOG {a['symbol']}: {a['reason']}")
                if res.get("skipped_stale_feed"):
                    log(f"WATCHDOG skipped (no live price): {', '.join(res['skipped_stale_feed'])}")
            except Exception as exc:
                stats["watchdog_errors"] += 1
                log(f"WATCHDOG tick FAILED: {type(exc).__name__}: {exc}")
            next_wd = max(t + wd_interval, time.monotonic())
        if cycle_thread is None or not cycle_thread.is_alive():      # ---- entry cycles on wall-clock boundaries
            jobs = due_jobs(time.time(), last_slot, five_sec, fifteen_sec)
            if jobs:
                cycle_thread = threading.Thread(target=cycle_worker, args=(jobs,), daemon=True, name="cycles")
                cycle_thread.start()
        if once and cycle_thread is not None and not cycle_thread.is_alive() and stats["watchdog_ticks"] >= 1:
            break
        sleep_fn(max(0.05, min(0.5, next_wd - time.monotonic())))
    if cycle_thread is not None and cycle_thread.is_alive():
        cycle_thread.join(timeout=90)
    log(f"Scheduler finished: {stats}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="NSE V4.8 continuous scheduler (20s watchdog + 5/15-min cycles)")
    ap.add_argument("--until-ist", default="15:35")
    ap.add_argument("--start-ist", default="09:00")
    ap.add_argument("--force-session", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="one watchdog tick + one cycle batch, then exit (smoke test)")
    ap.add_argument("--max-minutes", type=float, default=None, help="hard stop after N minutes (default 10 with --force-session, so tests cannot burn Actions minutes)")
    a = ap.parse_args()
    max_rt = a.max_minutes * 60 if a.max_minutes else (600.0 if a.force_session else None)
    run_scheduler(until_min=parse_hhmm(a.until_ist), start_min=parse_hhmm(a.start_ist), force_session=a.force_session, dry_run=a.dry_run, once=a.once, max_runtime=max_rt)


if __name__ == "__main__":
    main()
