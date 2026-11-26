"""Why isn't it trading?  Run:  python diagnose.py          (full: also dry-runs every desk, writes nothing to the ledger)
                              python diagnose.py --quick   (secrets + Google Sheet write test + price feed only)

Prints PASS / WARN / FAIL for every link in the chain and the exact rejection reason of every candidate."""
import argparse
import json
import os
import sys
import time

FAILS = 0


def line(level: str, title: str, detail: str = "") -> None:
    global FAILS
    FAILS += level == "FAIL"
    print(f"[{level:<4}] {title}" + (f"  ->  {detail}" if detail else ""), flush=True)


def check_env() -> None:
    sid = os.getenv("GOOGLE_SHEET_ID", "").strip()
    raw = (os.getenv("GCP_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    line("PASS" if sid else "WARN", "GOOGLE_SHEET_ID", "set" if sid else "missing: the app will guess a sheet by name or fall back to a local file (lost on GitHub Actions)")
    if not raw:
        line("WARN", "Service-account JSON", "missing (GCP_SERVICE_ACCOUNT_JSON): running with the local ledger file")
    else:
        try:
            info = json.loads(raw)
            line("PASS", "Service-account JSON", f"valid; share the sheet (as EDITOR) with {info.get('client_email', '?')}")
        except Exception as exc:
            line("FAIL", "Service-account JSON", f"not valid JSON: {exc}")
    line("PASS" if os.getenv("GEMINI_API_KEY") else "WARN", "GEMINI_API_KEY", "set" if os.getenv("GEMINI_API_KEY") else "missing: deterministic screen only (that is fine)")
    line("PASS", "LIVE_MODE / CAS", f"LIVE_MODE={os.getenv('LIVE_MODE', 'false')}  CAS_ENABLED={os.getenv('CAS_ENABLED', 'true')}")
    if os.getenv("CAS_ENABLED", "true").strip().lower() != "true":
        line("FAIL", "CAS_ENABLED", "is not 'true' -> every cycle is skipped by the Capital Allocation Switch")


def check_ledger():
    import headless_runner as hr
    from core.ledger import sheets_error_hint
    store = hr.LedgerStore()
    has_creds = bool((os.getenv("GCP_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip())
    if store.master_sh is None:
        line("FAIL" if has_creds else "WARN", "Google Sheet connection", store.status_msg)
        return store
    line("PASS", "Google Sheet connection", store.status_msg)
    try:                                                    # real write test: proves Editor access + a native Google Sheet
        ws = store.master_sh.worksheet("agent_messages")
        ws.append_rows([["DIAG", time.strftime("%Y-%m-%d %H:%M:%S"), "DIAGNOSTIC", "LEDGER", "DIAGNOSTIC", "write test from diagnose.py", "{}"]], value_input_option="USER_ENTERED")
        line("PASS", "Google Sheet WRITE test", "a DIAGNOSTIC row was appended to agent_messages (you can delete it)")
    except Exception as exc:
        line("FAIL", "Google Sheet WRITE test", sheets_error_hint(f"{type(exc).__name__}: {exc}"))
        return store
    try:
        st = store.load_all()
        line("PASS", "Ledger read", f"{len(st['trades'])} trades, {sum(1 for t in st['trades'] if str(t.get('status')).upper() == 'OPEN')} open")
    except Exception as exc:
        line("FAIL", "Ledger read", f"{type(exc).__name__}: {exc}")
    return store


def check_market() -> None:
    from core.market_data import get_live_quotes_batch, get_nse_session_status
    s = get_nse_session_status()
    line("PASS" if s["can_open_new_entries"] else "WARN", "Session gate", f"{s['state']} at {s['time_ist']} (new entries {'ALLOWED' if s['can_open_new_entries'] else 'BLOCKED'})")
    t = time.monotonic()
    q = get_live_quotes_batch(["RELIANCE.NS", "^NSEI", "^INDIAVIX"], force_live=True)
    feeds = {k: v.get("feed") for k, v in q.items()}
    bad = [k for k, f in feeds.items() if str(f).startswith(("fallback", "entry_anchor"))]
    line("FAIL" if bad else "PASS", "Live price feed", f"{feeds} in {time.monotonic() - t:.1f}s" + ("  -> Yahoo is blocking/rate-limiting: NO trades are possible (stale-feed protection)" if bad else ""))


def check_desks() -> None:
    import headless_runner as hr
    for desk in ("SWING", "INTRADAY", "FNO", "COMMODITY"):
        t = time.monotonic()
        try:
            r = hr.run_cycle(desk=desk, max_entries=1, ignore_session_gate=True, dry_run=True, skip_watchdog=True)
        except Exception as exc:
            line("FAIL", f"{desk} dry-run", f"{type(exc).__name__}: {exc}")
            continue
        dur = time.monotonic() - t
        if r.get("gate_blocked"):
            line("WARN", f"{desk} dry-run", f"BLOCKED by gate: {r['gate_blocked']}  ({dur:.1f}s)")
            continue
        level = "PASS" if r["entered"] else "WARN"
        line(level, f"{desk} dry-run", f"{dur:.1f}s | evaluated {r['evaluated']} | would enter {r['entered']} | regime {(r.get('regime') or {}).get('mode', '-')}")
        if dur > 60:
            line("WARN", f"{desk} speed", f"{dur:.0f}s per cycle is slow for a 5-minute cadence (budget CYCLE_BUDGET_SEC)")
        for k, v in sorted((r.get("rejection_summary") or {}).items(), key=lambda kv: -kv[1])[:6]:
            print(f"           rejected x{v}: {k}")
        for d in r.get("decisions", [])[:4]:
            print(f"           {d['symbol']:<16} conv={d['conviction']}  {'APPROVED' if d['approved'] else 'rejected: ' + '; '.join(d['reasons'])[:110]}")


def check_watchdog(store) -> None:
    import headless_runner as hr
    try:
        st = store.load_all()
        r = hr.run_watchdog_pass(store, st, dry_run=True)
        opened = [t for t in st["trades"] if str(t.get("status")).upper() == "OPEN"]
        line("PASS", "Watchdog dry-run", f"{len(opened)} open trades read, {r['evaluated_count']} evaluated with live prices, {r['actions_count']} actions, stale-feed skipped: {r.get('skipped_stale_feed') or 'none'}")
        for a in r["actions"]:
            print(f"           {a['symbol']}: {a['reason']}")
    except Exception as exc:
        line("FAIL", "Watchdog dry-run", f"{type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    print("=== NSE V4.8 diagnostics ===")
    check_env()
    store = check_ledger()
    check_market()
    if not a.quick:
        check_desks()
        check_watchdog(store)
    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(FAILS) + ' FAILURE(S) - fix the FAIL lines first'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
