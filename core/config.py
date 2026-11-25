import json
from dataclasses import dataclass, replace
from typing import Any, Dict, List


@dataclass
class DeskConfig:
    key: str
    label: str
    capital: float
    max_pos_pct: float
    risk_per_trade_pct: float
    soft_cushion_pct: float
    baseline_slots: int
    daily_loss_limit: float
    allow_cushion_tap: bool = True
    allow_overflow_slots: bool = False
    rsi_cap: float = 72.0
    min_rr: float = 1.60
    trail_be_pct: float = 25.0
    trail_step_pct: float = 10.0
    trail_lock_step_r: float = 0.25
    max_overflow_slots: int = 0   # extra slots above baseline (only when allow_overflow_slots)
    margin_pct: float = 1.0       # fraction of notional that is blocked as cash (1.0 = fully paid; futures use margin)
    breaker_liquidates: bool = True  # daily-loss breaker closes open positions (False = only blocks new entries)
    basket_floor: bool = True        # swing: liquidate the whole open basket when it falls below the stepped floor


DEFAULT_DESKS: Dict[str, DeskConfig] = {
    # V4.8.2 spec. Trail = 25% breakeven + 10% steps on every desk; entry cutoff 15:00 IST.
    # Swing: 15 baseline slots, +5 overflow for conviction >= 90. Swing breaker only blocks entries
    # (-Rs2,000 on a Rs10L book is 0.2%: liquidating a multi-day book on that would be noise-trading).
    "swing": DeskConfig(key="swing", label="Swing", capital=1000000.0, max_pos_pct=15.0, risk_per_trade_pct=1.0,
                        soft_cushion_pct=10.0, baseline_slots=15, daily_loss_limit=-2000.0,
                        allow_cushion_tap=True, allow_overflow_slots=True, max_overflow_slots=5,
                        rsi_cap=72.0, min_rr=1.60, trail_be_pct=25.0, trail_step_pct=10.0, breaker_liquidates=False),
    "intraday": DeskConfig(key="intraday", label="Intraday", capital=100000.0, max_pos_pct=25.0, risk_per_trade_pct=1.0,
                           soft_cushion_pct=10.0, baseline_slots=3, daily_loss_limit=-1500.0,
                           rsi_cap=75.0, min_rr=1.60, trail_be_pct=25.0, trail_step_pct=10.0),
    # F&O: one NIFTY lot (65) with a 20% premium stop risks ~Rs1,000 (1 day to expiry) up to ~Rs2,300 (6 days). A 2.5% budget (Rs2,500)
    # lets the desk trade the whole weekly cycle. One full stop-out (-Rs1,500+) trips the -Rs1,500 daily breaker by design.
    "fno": DeskConfig(key="fno", label="F&O", capital=100000.0, max_pos_pct=30.0, risk_per_trade_pct=2.5,
                      soft_cushion_pct=10.0, baseline_slots=2, daily_loss_limit=-1500.0,
                      rsi_cap=78.0, min_rr=1.60, trail_be_pct=25.0, trail_step_pct=10.0),
    # Commodity: MCX futures proxied from COMEX/NYMEX + USDINR; ~10% margin blocked per position.
    "commodity": DeskConfig(key="commodity", label="Commodity", capital=100000.0, max_pos_pct=25.0, risk_per_trade_pct=1.0,
                            soft_cushion_pct=10.0, baseline_slots=2, daily_loss_limit=-1500.0,
                            rsi_cap=74.0, min_rr=1.60, trail_be_pct=25.0, trail_step_pct=10.0, margin_pct=0.10),
}


# ---- user-editable settings (saved from the dashboard into the ledger `account` tab as JSON) ----
SETTINGS_BOUNDS: Dict[str, tuple] = {
    "capital": (0.0, 1e9), "max_pos_pct": (1.0, 100.0), "risk_per_trade_pct": (0.1, 5.0),
    "soft_cushion_pct": (0.0, 40.0), "baseline_slots": (1, 100), "max_overflow_slots": (0, 50), "margin_pct": (0.01, 1.0),
}
SETTINGS_BOOLS = ("allow_cushion_tap", "allow_overflow_slots", "breaker_liquidates", "basket_floor")


def apply_desk_overrides(cfg: "DeskConfig", overrides: Dict[str, Any]) -> "DeskConfig":
    """Return a copy of cfg with validated user overrides applied (unknown/invalid keys ignored)."""
    clean: Dict[str, Any] = {}
    for k, (lo, hi) in SETTINGS_BOUNDS.items():
        if k in (overrides or {}):
            try:
                v = float(overrides[k])
            except (TypeError, ValueError):
                continue
            v = min(max(v, lo), hi)
            clean[k] = int(v) if k in ("baseline_slots", "max_overflow_slots") else v
    for k in SETTINGS_BOOLS:
        if k in (overrides or {}):
            clean[k] = bool(overrides[k])
    return replace(cfg, **clean) if clean else cfg


def parse_desk_settings(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        return {str(k).lower(): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def get_effective_desk_config(desk_name: str, accounts: Dict[str, Any] | None = None) -> "DeskConfig":
    """Desk config + whatever the user saved from the dashboard (this is what the runner must use)."""
    cfg = get_desk_config(desk_name)
    return apply_desk_overrides(cfg, parse_desk_settings((accounts or {}).get("desk_settings")).get(cfg.key, {}))


def normalize_desk_key(name: Any) -> str:
    """'F&O' / 'f&o' / 'FNO' / 'fo' -> 'fno'; 'Swing' -> 'swing' ... (the single place desk labels are normalised)."""
    d = str(name or "swing").strip().lower().replace("&", "").replace(" ", "")
    return "fno" if d == "fo" else d


def get_desk_config(desk_name: str) -> DeskConfig:
    norm = normalize_desk_key(desk_name)
    if norm in ("fo", "fno"):
        return DEFAULT_DESKS["fno"]
    return DEFAULT_DESKS.get(norm, DEFAULT_DESKS["swing"])


AGENT_HIERARCHY: List[Dict[str, str]] = [
    {"id": "AG1_LOW_RISK", "short": "AG1", "role": "Low-Risk Conservative Scout", "tier": "LOW"},
    {"id": "AG2_MID_RISK", "short": "AG2", "role": "Mid-Risk Balanced Momentum Scout", "tier": "MID"},
    {"id": "AG3_HIGH_RISK", "short": "AG3", "role": "High-Risk Alpha & Breakout Scout", "tier": "HIGH"},
    {"id": "AG4_CHIEF_ANALYST", "short": "AG4", "role": "Chief Audit, Sizing & Regime Judge", "tier": "AUDIT"},
    {"id": "AG5_EXECUTION_BROKER", "short": "AG5", "role": "Paper Execution & Watchdog Guard", "tier": "EXEC"},
]

SWING_TRADES_HEADERS: List[str] = [
    "timestamp", "exit_timestamp", "symbol", "sector", "tier", "side", "status",
    "qty", "entry", "initial_sl", "sl", "target", "exit_price",
    "gross_pnl", "charges_slippage", "pnl", "realized_r", "mfe_pct", "mae_pct",
    "version", "desk", "allocation_tag", "reason",
]

UNIFIED_2L_TRADES_HEADERS: List[str] = [
    "timestamp", "exit_timestamp", "book", "symbol", "sector", "strategy", "side", "status",
    "qty_or_lots", "lot_size", "entry", "initial_sl", "sl", "target", "exit_price",
    "gross_pnl", "charges_slippage", "net_pnl", "realized_r", "mfe_pct", "mae_pct",
    "version", "desk", "allocation_tag", "reasoning",
]

V48_TRADES_EXTRA_HEADERS: List[str] = [
    "exit_timestamp", "sector", "side", "initial_sl", "gross_pnl", "charges_slippage",
    "version", "desk", "allocation_tag", "mfe_pct", "mae_pct", "realized_r",
]

MAX_POSITIONS_PER_SECTOR: int = 3
EARNINGS_BLACKOUT_HOURS: int = 48
ENTRY_CUTOFF_IST_HOUR: int = 15
ENTRY_CUTOFF_IST_MINUTE: int = 0
EOD_SQUAREOFF_IST_HOUR: int = 15
EOD_SQUAREOFF_IST_MINUTE: int = 15

SWING_STEPPED_FLOOR_TRIGGER: float = 7000.0
INTRADAY_FLOOR_STEPS: List[tuple] = [(3000.0, 1500.0), (4000.0, 3000.0), (5000.0, 0.75)]
GRADUATION_MIN_CLOSED_TRADES: int = 100
GRADUATION_MIN_PROFIT_FACTOR: float = 1.50

MASTER_TRADES_HEADERS: List[str] = [
    "timestamp", "exit_timestamp", "desk", "book", "symbol", "sector", "tier", "strategy",
    "side", "status", "qty", "qty_or_lots", "lot_size", "entry", "initial_sl", "sl",
    "target", "initial_target", "exit_price", "gross_pnl", "charges_slippage", "pnl", "net_pnl",
    "realized_r", "mfe_pct", "mae_pct", "version", "allocation_tag", "reason",
]


# ---------------- V4.8.2 strategy constants ----------------
MIN_CONVICTION: int = 65

# Swing: dead-money time stop, target rollover, stepped basket floor (open-basket net P&L)
SWING_TIME_STOP_DAYS: int = 7
SWING_ROLLOVER_BUFFER: float = 0.0025      # new SL = old_target * (1 - 0.25%)
SWING_ROLLOVER_EXT: float = 0.5            # new target = old_target + 0.5 * (old_target - entry)
SWING_FLOOR_STEPS: List[tuple] = [(7000.0, 3500.0), (10000.0, 6500.0), (15000.0, 10500.0), (20000.0, 0.75)]

# Swing market regime (Nifty): DEFENSIVE if day change <= -0.60%, Nifty < 20-SMA, or India VIX >= 22
REGIME_CHG_PCT: float = -0.60
REGIME_VIX_MAX: float = 22.0
DEFENSIVE_RISK_MULT: float = 0.5
DEFENSIVE_ALLOWED_TIERS: tuple = ("LOW",)

# F&O (verify lot sizes against the latest NSE circular before going live)
FNO_LOT_SIZES: Dict[str, int] = {"NIFTY": 65, "BANKNIFTY": 30}
FNO_IV_SKEW: Dict[str, float] = {"NIFTY": 1.00, "BANKNIFTY": 1.10}   # IV = India VIX * skew
FNO_MIN_PREMIUM: float = 15.0
FNO_SL_PCT: float = 0.20
FNO_TARGET_RR: float = 2.1
FNO_MIN_ADX: float = 18.0
FNO_EXPIRY_NO_BUY_IST: tuple = (13, 30)    # no new option buying on expiry day after 13:30 IST
FALLBACK_VIX: float = 14.0

# Commodity proxies (MCX contracts approximated from international futures converted to INR)
GOLD_IMPORT_DUTY_FACTOR: float = 1.06      # MCX gold trades at a premium to parity (duty); moves track COMEX
