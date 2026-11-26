"""Trailing-stop ladder shared by the live watchdog and the backtester (single source of truth)."""
from typing import Optional


def compute_trailing_sl(side: str, entry: float, sl: float, target_span: float, ltp: float, prog_pct: float,
                        be_pct: float, step_pct: float, per_share_charges: float) -> Optional[float]:
    """Return the new (tighter) stop, or None if the stop should not move.

    Spec: at `be_pct` % of target progress -> breakeven + charges; every further `step_pct` % locks
    (be_pct + step_pct*(n-1)) % of the target distance.
    """
    if prog_pct < be_pct:
        return None
    step_pct = max(0.1, float(step_pct))
    steps = int((prog_pct - be_pct) // step_pct)
    lock_frac = (be_pct + step_pct * (steps - 1)) / 100.0 if steps > 0 else 0.0
    if str(side).upper() == "SHORT":
        raw = round(entry - per_share_charges, 2) if steps <= 0 else round(entry - lock_frac * target_span, 2)
        cur = sl if sl > 0 else entry
        cand = round(max(ltp * 1.002, min(cur, raw)), 2)
        return cand if cand < cur - 0.01 else None
    raw = round(entry + per_share_charges, 2) if steps <= 0 else round(entry + lock_frac * target_span, 2)
    cand = round(min(ltp * 0.998, max(sl, raw)), 2)
    return cand if cand > sl + 0.01 else None
