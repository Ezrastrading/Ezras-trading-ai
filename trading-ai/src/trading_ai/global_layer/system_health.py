"""Dashboard-oriented aggregates — bots, spend, failures (read-only computation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.budget_governor import load_budget_state


def build_system_health(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    by_avenue: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    stalled: List[str] = []
    degraded: List[str] = []
    for b in bots:
        av = str(b.get("avenue") or "?")
        role = str(b.get("role") or "?")
        by_avenue[av] = by_avenue.get(av, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
        st = str(b.get("lifecycle_state") or "")
        if st in ("shadow", "probation") and float((b.get("performance") or {}).get("utility_score") or 0.0) < 0.2:
            stalled.append(str(b.get("bot_id")))
        if st == "degraded":
            degraded.append(str(b.get("bot_id")))

    bud = load_budget_state()
    return {
        "truth_version": "system_health_v1",
        "active_bot_count": len(bots),
        "by_avenue": by_avenue,
        "by_role": by_role,
        "stalled_bots": stalled,
        "degraded_bots": degraded,
        "token_snapshot": {
            "global_daily_token_budget": bud.get("global_daily_token_budget"),
            "ai_calls_this_hour": bud.get("ai_calls_this_hour"),
        },
        "coverage_gaps": [],
        "honesty": "Token usage per-bot requires ledger integration; fields are policy defaults until wired.",
    }
