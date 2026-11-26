from typing import Any, Dict, List
from core.config import GRADUATION_MIN_CLOSED_TRADES, GRADUATION_MIN_PROFIT_FACTOR


def _calc_trust_from_trades(subset: List[Dict[str, Any]], role_mode: str = "SCOUT") -> Dict[str, Any]:
    if not subset:
        return {"trust_score": 50, "delta": 0.0, "win_rate": 0.0, "avg_r": 0.0, "closed_count": 0, "wins": 0, "losses": 0, "realized_net_pnl": 0.0}

    wins = sum(1 for t in subset if float(t.get("pnl", 0) or 0) > 0)
    losses = sum(1 for t in subset if float(t.get("pnl", 0) or 0) < 0)
    clamped_rs = [max(-3.0, min(4.0, float(t.get("realized_r", 0) or 0))) for t in subset]
    avg_r_clamped = sum(clamped_rs) / len(clamped_rs)
    avg_r_raw = sum(float(t.get("realized_r", 0) or 0) for t in subset) / len(subset)
    net_pnl = round(sum(float(t.get("pnl", 0) or 0) for t in subset), 2)

    if role_mode == "CHIEF_ANALYST":
        raw_trust = 50.0 + (avg_r_clamped * 8.5) + ((wins - losses) * 1.2)
    elif role_mode == "EXECUTION_WATCHDOG":
        full_sl_losses = sum(1 for t in subset if str(t.get("status", "")).upper() == "SL_HIT" and float(t.get("pnl", 0) or 0) < 0)
        protected_exits = sum(1 for t in subset if any(k in str(t.get("status", "")).upper() for k in ("TARGET", "TRAILING", "FLOOR")))
        raw_trust = 50.0 + (protected_exits * 2.0) - (full_sl_losses * 2.5) + (avg_r_clamped * 4.0)
    else:
        raw_trust = 50.0 + (wins * 2.0) - (losses * 2.2) + (avg_r_clamped * 5.0)

    trust = int(round(max(35.0, min(98.0, raw_trust))))
    return {"trust_score": trust, "delta": round(trust - 50.0, 1), "win_rate": round((wins / len(subset)) * 100.0, 1), "avg_r": round(avg_r_raw, 2), "closed_count": len(subset), "wins": wins, "losses": losses, "realized_net_pnl": net_pnl}


def compute_desk_and_agent_trust(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed_all = [t for t in trades if str(t.get("status", "")).upper() != "OPEN"]
    v48_closed = [t for t in closed_all if str(t.get("version", "")).lower() == "v4.8"]

    agent_specs = [
        ("AG1_LOW_RISK", "LOW", "Low-Risk Scout", "SCOUT"),
        ("AG2_MID_RISK", "MID", "Mid-Risk Scout", "SCOUT"),
        ("AG3_HIGH_RISK", "HIGH", "High-Risk Scout", "SCOUT"),
        ("AG4_CHIEF_ANALYST", "ALL", "Chief Analyst", "CHIEF_ANALYST"),
        ("AG5_EXECUTION_BROKER", "ALL", "Execution & Watchdog", "EXECUTION_WATCHDOG"),
    ]

    desk_scores: Dict[str, Dict[str, Any]] = {}
    for desk_name in ["Swing", "Intraday", "F&O", "Commodity"]:
        subset = [t for t in closed_all if str(t.get("desk", "Swing")).lower() == desk_name.lower()]
        metrics = _calc_trust_from_trades(subset, role_mode="SCOUT")
        metrics["desk"] = desk_name
        per_desk_agents: List[Dict[str, Any]] = []
        for agent_id, tier, label, r_mode in agent_specs:
            ag_sub = subset if tier == "ALL" else [t for t in subset if str(t.get("tier", "")).upper() == tier]
            ag_m = _calc_trust_from_trades(ag_sub, role_mode=r_mode)
            per_desk_agents.append({"agent_id": agent_id, "label": label, "tier": tier, "samples": len(ag_sub), **ag_m})
        metrics["agents"] = per_desk_agents
        desk_scores[desk_name] = metrics

    if desk_scores["Commodity"]["closed_count"] > 0:
        composite_trust = int(round(
            desk_scores["Swing"]["trust_score"] * 0.35 + desk_scores["Intraday"]["trust_score"] * 0.25
            + desk_scores["F&O"]["trust_score"] * 0.25 + desk_scores["Commodity"]["trust_score"] * 0.15
        ))
    else:
        composite_trust = int(round(
            desk_scores["Swing"]["trust_score"] * 0.40 + desk_scores["Intraday"]["trust_score"] * 0.30 + desk_scores["F&O"]["trust_score"] * 0.30
        ))

    agent_scores: List[Dict[str, Any]] = []
    for agent_id, tier, label, r_mode in agent_specs:
        ag_sub = closed_all if tier == "ALL" else [t for t in closed_all if str(t.get("tier", "")).upper() == tier]
        ag_m = _calc_trust_from_trades(ag_sub, role_mode=r_mode)
        agent_scores.append({"agent_id": agent_id, "label": label, "tier": tier, "samples": len(ag_sub), **ag_m})

    gross_wins = sum(float(t.get("pnl", 0) or 0) for t in closed_all if float(t.get("pnl", 0) or 0) > 0)
    gross_losses = abs(sum(float(t.get("pnl", 0) or 0) for t in closed_all if float(t.get("pnl", 0) or 0) < 0))
    profit_factor = round(gross_wins / max(gross_losses, 1.0), 2) if closed_all else 0.0

    mfe_vals = [float(t.get("mfe_pct", 0) or 0) for t in closed_all if float(t.get("mfe_pct", 0) or 0) != 0]
    mae_vals = [float(t.get("mae_pct", 0) or 0) for t in closed_all if float(t.get("mae_pct", 0) or 0) != 0]

    return {
        "schema_scope": "v4.8",
        "total_closed_count": len(closed_all),
        "v48_closed_count": len(v48_closed),
        "composite_trust": composite_trust,
        "profit_factor": profit_factor,
        "graduation_ready": len(v48_closed) >= GRADUATION_MIN_CLOSED_TRADES and profit_factor >= GRADUATION_MIN_PROFIT_FACTOR,
        "avg_mfe_pct": round(sum(mfe_vals) / len(mfe_vals), 2) if mfe_vals else 0.0,
        "avg_mae_pct": round(sum(mae_vals) / len(mae_vals), 2) if mae_vals else 0.0,
        "desks": desk_scores,
        "agents": agent_scores,
    }
