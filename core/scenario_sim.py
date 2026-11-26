import random
from typing import Any, Dict, List, Optional


def run_bootstrap_simulation(
    entry: float, sl: float, target: float, qty: int, charges: float = 120.0,
    paths: int = 500, seed: int = 48, historical_returns: Optional[List[float]] = None,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    risk_unit = max(0.05, abs(entry - sl))
    target_hits = 0
    stop_hits = 0
    net_outcomes: List[float] = []
    sample_fans: List[List[float]] = []
    use_hist = bool(historical_returns and len(historical_returns) >= 10)

    for p in range(paths):
        price = entry
        series = [round(price, 2)]
        outcome = 0.0
        for _ in range(12):
            if use_hist:
                ret = rng.choice(historical_returns)  # type: ignore[arg-type]
                price = max(0.05, price * (1.0 + ret))
            else:
                step = rng.gauss(0.0, 0.42 * risk_unit)
                price = max(0.05, price + step)
            series.append(round(price, 2))
            if price >= target:
                target_hits += 1
                outcome = ((target - entry) * qty) - charges
                break
            if price <= sl:
                stop_hits += 1
                outcome = ((sl - entry) * qty) - charges
                break
        else:
            outcome = ((price - entry) * qty) - charges
        net_outcomes.append(outcome)
        if p < 16:
            sample_fans.append(series)

    target_pct = round((target_hits / max(paths, 1)) * 100.0, 1)
    stop_pct = round((stop_hits / max(paths, 1)) * 100.0, 1)
    exp_pnl = round(sum(net_outcomes) / max(len(net_outcomes), 1), 2)
    return {
        "paths": paths,
        "mode": "historical_returns_bootstrap" if use_hist else "zero_drift_volatility_bootstrap",
        "target_first_pct": target_pct, "stop_first_pct": stop_pct,
        "timeout_pct": round(max(0.0, 100.0 - target_pct - stop_pct), 1),
        "expected_net_pnl": exp_pnl, "advisory_only": True, "fan_paths": sample_fans,
    }
