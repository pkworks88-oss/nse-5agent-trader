"""Command-line backtester (needs internet for Yahoo data).

  python run_backtest.py swing --years 3
  python run_backtest.py intraday
  python run_backtest.py swing --years 3 --set basket_floor=false --set risk_per_trade_pct=0.75

Writes results/<desk>_summary.json, _trades.csv and _equity.csv
"""
import argparse
import json
import os

import pandas as pd

from core.backtest_engine import run_intraday_backtest_from_yahoo, run_swing_backtest_from_yahoo


def _parse_sets(items):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = (v.strip().lower() == "true") if v.strip().lower() in ("true", "false") else float(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("desk", choices=["swing", "intraday"])
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--set", action="append", help="override a desk setting, e.g. --set risk_per_trade_pct=0.75")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    ov = _parse_sets(a.set)
    res = run_swing_backtest_from_yahoo(years=a.years, overrides=ov) if a.desk == "swing" else run_intraday_backtest_from_yahoo(overrides=ov)
    os.makedirs(a.out, exist_ok=True)
    pd.DataFrame(res["trades"]).to_csv(f"{a.out}/{a.desk}_trades.csv", index=False)
    pd.DataFrame(res["equity"]).to_csv(f"{a.out}/{a.desk}_equity.csv", index=False)
    with open(f"{a.out}/{a.desk}_summary.json", "w") as f:
        json.dump({k: res[k] for k in ("engine", "desk", "capital", "settings", "metrics", "caveats")}, f, indent=2, default=str)
    print(json.dumps(res["metrics"], indent=2, default=str))
    print("\nCAVEATS:\n- " + "\n- ".join(res["caveats"]))


if __name__ == "__main__":
    main()
