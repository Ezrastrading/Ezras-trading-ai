"""Lifecycle transitions — promoted bots only for high-influence decisions (policy)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.bot_types import BotLifecycleState

_ALLOWED = {s.value for s in BotLifecycleState}

_TRANSITIONS: Dict[str, List[str]] = {
    BotLifecycleState.PROPOSED.value: [BotLifecycleState.INITIALIZED.value, BotLifecycleState.ARCHIVED.value],
    BotLifecycleState.INITIALIZED.value: [BotLifecycleState.SHADOW.value, BotLifecycleState.PROBATION.value],
    BotLifecycleState.SHADOW.value: [
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.ELIGIBLE.value,
        BotLifecycleState.RETIRED.value,
    ],
    BotLifecycleState.ELIGIBLE.value: [
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.RETIRED.value,
    ],
    BotLifecycleState.PROBATION.value: [
        BotLifecycleState.ACTIVE.value,
        BotLifecycleState.DEGRADED.value,
        BotLifecycleState.DEMOTED.value,
    ],
    BotLifecycleState.ACTIVE.value: [
        BotLifecycleState.PROMOTED.value,
        BotLifecycleState.DEGRADED.value,
        BotLifecycleState.PAUSED.value,
        BotLifecycleState.FROZEN.value,
        BotLifecycleState.DEMOTED.value,
    ],
    BotLifecycleState.PROMOTED.value: [
        BotLifecycleState.DEGRADED.value,
        BotLifecycleState.RETIRED.value,
        BotLifecycleState.PAUSED.value,
        BotLifecycleState.FROZEN.value,
        BotLifecycleState.DEMOTED.value,
    ],
    BotLifecycleState.PAUSED.value: [
        BotLifecycleState.ACTIVE.value,
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.RETIRED.value,
        BotLifecycleState.ARCHIVED.value,
    ],
    BotLifecycleState.FROZEN.value: [
        BotLifecycleState.DEGRADED.value,
        BotLifecycleState.ARCHIVED.value,
        BotLifecycleState.PAUSED.value,
    ],
    BotLifecycleState.DEMOTED.value: [
        BotLifecycleState.SHADOW.value,
        BotLifecycleState.PROBATION.value,
        BotLifecycleState.ARCHIVED.value,
    ],
    BotLifecycleState.DEGRADED.value: [BotLifecycleState.PROBATION.value, BotLifecycleState.RETIRED.value],
    BotLifecycleState.RETIRED.value: [BotLifecycleState.ARCHIVED.value],
    BotLifecycleState.ARCHIVED.value: [],
}


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in _TRANSITIONS.get(from_state, [])


def propose_transition(bot: Dict[str, Any], to_state: str, *, promotion_score_min: float = 0.6) -> Tuple[bool, str]:
    cur = str(bot.get("lifecycle_state") or BotLifecycleState.PROPOSED.value)
    if to_state not in _ALLOWED:
        return False, "invalid_target_state"
    if not can_transition(cur, to_state):
        return False, f"transition_not_allowed:{cur}->{to_state}"
    if to_state in (BotLifecycleState.PROMOTED.value, BotLifecycleState.ACTIVE.value):
        perf = bot.get("performance") or {}
        comp = (perf.get("composite") or {}) if isinstance(perf, dict) else {}
        ps = float(comp.get("promotion_score") or perf.get("promotion_score") or 0.0)
        if ps < promotion_score_min:
            return False, "insufficient_promotion_score"
    if to_state == BotLifecycleState.ELIGIBLE.value:
        sc = bot.get("promotion_scorecard") if isinstance(bot.get("promotion_scorecard"), dict) else {}
        pr = float(sc.get("promotion_readiness_score") or 0.0)
        if pr < promotion_score_min:
            return False, "insufficient_readiness_for_eligible"
    return True, "ok"


def set_lifecycle(bot: Dict[str, Any], to_state: str, **extra: Any) -> Dict[str, Any]:
    ok, why = propose_transition(bot, to_state)
    if not ok:
        raise ValueError(why)
    out = dict(bot)
    out["lifecycle_state"] = to_state
    out.update(extra)
    return out
