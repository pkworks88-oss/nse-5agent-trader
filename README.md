# NSE Multi-Asset AI Trading Platform — V4.8.2 (paper mode)

## Spec
| Item | Value |
|---|---|
| Trailing stop (all desks) | Breakeven (+charges) at 25% of target progress, then +10% steps (`core/trailing.py`, shared by live + backtest) |
| Intraday + F&O floor | peak ₹3,000 → floor ₹1,500; ₹4,000 → ₹3,000; ₹5,000+ → 75% of peak. Resets every IST day; entries halt while below the floor |
| Entry cutoff | 15:00 IST (EOD square-off 15:15) |
| Circuit breaker (daily loss) | Swing −₹2,000 (blocks entries; swing day-P&L = today's move of the book) · Intraday −₹1,500 · F&O −₹1,500 · Commodity −₹1,500 → these three **liquidate open positions** |
| Swing slots | 15 baseline, +5 overflow for conviction ≥ 90 |
| Swing regime filter | DEFENSIVE if Nifty < 20-SMA, day change ≤ −0.60% or VIX ≥ 22 → LOW tier only at half risk; no live Nifty data → no new swing entries |
| Swing time stop | 7 trading days with no profit locked → exit at 15:15 |
| Swing target rollover | At T1: SL → 99.75% of T1, T2 = T1 + 0.5·(T1 − entry), one roll |
| Swing basket floor | open-basket net P&L peak ₹7k → ₹3.5k, ₹10k → ₹6.5k, ₹15k → ₹10.5k, ₹20k+ → 75% (toggle `basket_floor`) |

## Desks
* **F&O** — ATM NIFTY (weekly, Tue) / BANKNIFTY (monthly) options; Black-Scholes premium from live spot + India VIX + real time-to-expiry; wick-accurate high/low; real lot sizes (`FNO_LOT_SIZES`, verify against the NSE circular); direction from index trend + ADX (chop → no trade); no new buying after 13:30 on expiry day; min premium ₹15. Risk budget 2.5% (₹2,500). BankNifty's monthly ATM lot needs more capital and is blocked by sizing (not mis-sized).
* **Commodity** — funded ₹1,00,000, ~10% margin per position. `GOLDPETAL MCX` (₹/gram, lot 1 g) and `CRUDEOILM MCX` (₹/bbl, lot 10 bbl) are *proxies*: COMEX/NYMEX futures × USDINR (gold × 1.06 duty factor). Moves track MCX; absolute level differs. Runs on NSE hours.
* **Dashboard settings** (capital, max position, risk %, cushion, slots) are saved to the ledger `account` tab (`desk_settings`) and read by the runner each cycle. Sizing compounds: capital + realized P&L.

## Real backtests
`python run_backtest.py swing --years 3` · `python run_backtest.py intraday` · or the page *Audit / Time Machine → Real Historical Backtest*.
Uses the live scoring, sizing, audit, charges, trailing, rollover, time-stop, floors and breakers. Signals on completed bars, fills at the next open, stop before target inside a bar, gaps fill at the open. Reports PF, expectancy (₹/R), drawdown, Sharpe, in-/out-of-sample split, bootstrap drawdown, benchmark and the graduation test. F&O and Commodity cannot be backtested honestly (no historical option/MCX data). A/B any setting: `--set basket_floor=false --set risk_per_trade_pct=0.75`.

## Tests (offline)
`python tests/test_v481.py && python tests/test_v482.py && python tests/test_backtest.py && python tests/test_e2e.py`

## Known limits
Hard-coded stock universe (survivorship bias in backtests); NSE holiday list covers 2026 only; Gemini/news/earnings cannot be replayed; commodity prices are proxies; live broker adapters are intentionally not implemented (`LIVE_MODE` rejects orders).

## Running automatically (V4.8.3)
* **Check first:** `python diagnose.py` (full) or `python diagnose.py --quick`. It tests secrets, a real Google-Sheet write, the live price feed, the market gates and dry-runs every desk, printing the exact rejection reason of each candidate. Nothing is written to the trade ledger.
* **Best:** run `python run_local_loop.py` on your own PC/VPS during market hours. One process: the 20-second watchdog has its own clock; cycles run in a worker thread at :00/:05/:10… (INTRADAY, FNO, COMMODITY) and :00/:15/:30/:45 (SWING).
* **GitHub Actions:** `.github/workflows/v48_cycles.yml` starts two long sessions per day (09:10–12:40 and 12:40–15:35 IST) running the same loop. Free *private* repos get only 2,000 Actions minutes/month (this needs ~385 min per trading day), so use a public repo or your own machine.
* Tunables (env): `CYCLE_BUDGET_SEC` (90), `MAX_CANDIDATES_PER_CYCLE` (12), `GEMINI_TIMEOUT_MS` (8000).
