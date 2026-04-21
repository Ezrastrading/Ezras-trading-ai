"""Global portfolio risk layer — aggregates across avenues (deterministic envelope)."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.capital_governor import load_capital_governor_policy


def evaluate_global_portfolio_risk(*, registry_path=None) -> Dict[str, Any]:
    """
    Placeholder aggregation until unified position store exists.
    Returns structured pass/fail for system-wide concentration + venue dependency heuristics.
    """
    pol = load_capital_governor_policy()
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    by_avenue: Dict[str, int] = {}
    for b in bots:
        a = str(b.get("avenue") or "unknown")
        by_avenue[a] = by_avenue.get(a, 0) + 1
    ag = pol.get("aggregate_caps") or {}
    issues: List[str] = []
    if len(by_avenue) == 1 and len(bots) > 3:
        issues.append("single_avenue_concentration_heuristic")
    ok = len(issues) == 0
    return {
        "truth_version": "global_portfolio_risk_v1",
        "passed": ok,
        "issues": issues,
        "bots_per_avenue": by_avenue,
        "notes": "Wire to unified position + correlation matrix when available; never bypass capital governor.",
    }
