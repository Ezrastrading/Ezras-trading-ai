"""
Deterministic, rule-table doctrine evaluation (no LLMs).

Maps structured signals from change payloads to per-dimension PASS | WARN | FAIL,
then to aggregate verdict ALIGNED | PARTIALLY_ALIGNED | DRIFTING | DOCTRINE_VIOLATION.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Set, Tuple

DimensionLevel = Literal["PASS", "WARN", "FAIL"]
AggregateVerdict = Literal["ALIGNED", "PARTIALLY_ALIGNED", "DRIFTING", "DOCTRINE_VIOLATION"]

DOCTRINE_DIMENSIONS: Tuple[str, ...] = (
    "truthfulness",
    "whole_system_alignment",
    "anti_local_optimization",
    "capital_preservation",
    "portability_operator_ownership",
    "non_concealment",
    "non_conflict_between_agents",
)


@dataclass(frozen=True)
class DoctrineRule:
    rule_id: str
    dimension: str
    # If any of these signals present, apply effect (unless exempt_signals)
    trigger_signals: FrozenSet[str]
    effect: DimensionLevel  # FAIL or WARN on that dimension
    explanation: str
    escalates_to: AggregateVerdict  # ceiling when this rule fires
    exempt_signals: FrozenSet[str] = frozenset()


# Signals are normalized snake_case tokens: e.g. token:ignore_risk, key:bypass_governance
def _flatten(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj.lower()
    if isinstance(obj, Mapping):
        parts: List[str] = []
        for k, v in obj.items():
            parts.append(str(k).lower())
            parts.append(_flatten(v))
        return " ".join(parts)
    if isinstance(obj, (list, tuple, set)):
        return " ".join(_flatten(x) for x in obj)
    return str(obj).lower()


_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def extract_signals(
    *,
    change_type: str,
    payload: Optional[Mapping[str, Any]],
    context: Optional[Mapping[str, Any]],
) -> Set[str]:
    """Derive deterministic signal set from change metadata and nested text."""
    sig: Set[str] = set()
    sig.add(f"change_type:{change_type}")
    pl = dict(payload or {})
    ctx = dict(context or {})
    blob = _flatten({"payload": pl, "context": ctx})

    # Structured boolean / string keys (explicit doctrine-relevant flags)
    for key in (
        "bypass_governance",
        "bypass_phase8",
        "conceal_failure",
        "conceal_uncertainty",
        "hide_loss",
        "disable_kill_switch",
        "disable_audit",
        "self_modify_mandate",
        "ignore_operator",
        "local_pnl_only",
        "suppress_uncertainty",
        "parameter_change_outside_governance",
    ):
        if pl.get(key) is True or ctx.get(key) is True:
            sig.add(f"key:{key}")
        if str(pl.get(key, "")).lower() in ("true", "1", "yes"):
            sig.add(f"key:{key}")

    if pl.get("operator_approved") is True or ctx.get("operator_approved") is True:
        sig.add("key:operator_approved")
    if pl.get("governance_recorded") is True:
        sig.add("key:governance_recorded")

    # Normalized tokens from prose (deterministic word extraction)
    for m in _TOKEN_RE.finditer(blob):
        t = m.group(0)
        if len(t) >= 4:
            sig.add(f"tok:{t}")

    # High-impact phrases → canonical signals
    phrase_map: Tuple[Tuple[str, str], ...] = (
        ("ignore risk", "ignore_risk"),
        ("bypass governance", "bypass_governance"),
        ("hide loss", "hide_loss"),
        ("conceal", "conceal_failure"),
        ("secret objective", "hidden_objective"),
        ("maximize volume regardless", "local_volume_over_system"),
        ("disable kill switch", "disable_kill_switch"),
        ("turn off audit", "disable_audit"),
        ("self-modify mandate", "self_modify_mandate"),
        ("martingale", "martingale"),
        ("infinite leverage", "infinite_leverage"),
        ("ignore drawdown", "ignore_drawdown"),
        ("override operator", "override_operator"),
        ("ignore phase8", "bypass_phase8"),
        ("fully autonomous", "unbounded_autonomy"),
        ("suppress uncertainty", "suppress_uncertainty"),
        ("local pnl", "local_pnl_priority"),
    )
    for phrase, canonical in phrase_map:
        if phrase in blob:
            sig.add(f"phrase:{canonical}")

    if change_type == "bot_creation" and "phrase:unbounded_autonomy" in sig:
        if "operator" not in blob and "key:operator_approved" not in sig:
            sig.add("unbounded_autonomy_without_operator_ack")

    return sig


def _default_rules() -> List[DoctrineRule]:
    ts = frozenset
    return [
        DoctrineRule(
            "R_TRUTH_01",
            "truthfulness",
            ts({"phrase:conceal_failure", "key:conceal_failure", "phrase:hide_loss", "key:hide_loss"}),
            "FAIL",
            "Concealing losses or truth conflicts with doctrine.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_TRUTH_02",
            "truthfulness",
            ts({"phrase:suppress_uncertainty", "key:suppress_uncertainty"}),
            "FAIL",
            "Suppressing uncertainty violates surfacing requirements.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_SYS_01",
            "whole_system_alignment",
            ts(
                {
                    "phrase:bypass_governance",
                    "key:bypass_governance",
                    "phrase:override_operator",
                    "key:ignore_operator",
                }
            ),
            "FAIL",
            "Bypassing governance or overriding operator breaks whole-system alignment.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_LOCAL_01",
            "anti_local_optimization",
            ts(
                {
                    "phrase:local_volume_over_system",
                    "phrase:local_pnl_priority",
                    "key:local_pnl_only",
                }
            ),
            "FAIL",
            "Optimizing local volume or PnL over system mandate is forbidden.",
            "DRIFTING",
        ),
        DoctrineRule(
            "R_CAP_01",
            "capital_preservation",
            ts(
                {
                    "phrase:martingale",
                    "phrase:infinite_leverage",
                    "phrase:ignore_drawdown",
                }
            ),
            "WARN",
            "Capital-hostile pattern detected — review required.",
            "DRIFTING",
        ),
        DoctrineRule(
            "R_CAP_02",
            "capital_preservation",
            ts({"phrase:ignore_risk"}),
            "FAIL",
            "Explicit ignore-risk conflicts with capital preservation.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_PORT_01",
            "portability_operator_ownership",
            ts({"key:disable_audit", "phrase:disable_audit"}),
            "FAIL",
            "Disabling audit undermines inspectability and operator ownership.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_NONC_01",
            "non_concealment",
            ts({"phrase:conceal_failure", "key:conceal_failure", "key:conceal_uncertainty"}),
            "FAIL",
            "Concealment of failure or uncertainty is prohibited.",
            "DOCTRINE_VIOLATION",
        ),
        DoctrineRule(
            "R_AG_01",
            "non_conflict_between_agents",
            ts({"unbounded_autonomy_without_operator_ack"}),
            "FAIL",
            "Autonomy without operator acknowledgment risks agent conflict.",
            "DRIFTING",
        ),
    ]


_RULES: List[DoctrineRule] = _default_rules()


def evaluate_doctrine_scorecard(
    *,
    change_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
    extra_rules: Optional[Sequence[DoctrineRule]] = None,
) -> Dict[str, Any]:
    """
    Return structured scorecard with dimensions, triggered rules, aggregate verdict, explanations.
    """
    RANK = {"ALIGNED": 0, "PARTIALLY_ALIGNED": 1, "DRIFTING": 2, "DOCTRINE_VIOLATION": 3}

    rules = list(_RULES)
    if extra_rules:
        rules.extend(extra_rules)

    signals = extract_signals(change_type=change_type, payload=payload, context=context)
    dim_level: Dict[str, DimensionLevel] = {d: "PASS" for d in DOCTRINE_DIMENSIONS}
    triggered: List[Dict[str, Any]] = []
    explanations: List[str] = []
    max_escalation: AggregateVerdict = "ALIGNED"

    order = {"PASS": 0, "WARN": 1, "FAIL": 2}

    for rule in rules:
        if not (signals & rule.trigger_signals):
            continue
        if rule.exempt_signals and (signals & rule.exempt_signals):
            continue
        cur = dim_level.get(rule.dimension, "PASS")
        if order[rule.effect] > order[cur]:
            dim_level[rule.dimension] = rule.effect
        triggered.append(
            {
                "rule_id": rule.rule_id,
                "dimension": rule.dimension,
                "effect": rule.effect,
                "matched_signals": sorted(signals & rule.trigger_signals),
            }
        )
        explanations.append(f"{rule.rule_id}: {rule.explanation}")
        if RANK[rule.escalates_to] > RANK[max_escalation]:
            max_escalation = rule.escalates_to

    # Large unreviewed research / suggestion surface → PARTIALLY_ALIGNED
    blob = _flatten({"payload": payload or {}, "context": context or {}})
    if change_type in ("research_output", "improvement_suggestion") and len(blob) > 2000:
        if dim_level["truthfulness"] == "PASS":
            dim_level["truthfulness"] = "WARN"
        if max_escalation == "ALIGNED":
            max_escalation = "PARTIALLY_ALIGNED"
        explanations.append("SURFACE_01: large unreviewed text — manual review recommended.")
        triggered.append(
            {
                "rule_id": "SURFACE_01",
                "dimension": "truthfulness",
                "effect": "WARN",
                "matched_signals": ["large_payload"],
            }
        )

    # Map dimensions to aggregate if not already escalated by rules
    any_fail = any(v == "FAIL" for v in dim_level.values())
    any_warn = any(v == "WARN" for v in dim_level.values())
    if any_fail:
        critical_fail = dim_level["truthfulness"] == "FAIL" or dim_level["capital_preservation"] == "FAIL"
        if critical_fail and RANK[max_escalation] < RANK["DOCTRINE_VIOLATION"]:
            max_escalation = "DOCTRINE_VIOLATION"
        elif max_escalation == "ALIGNED":
            max_escalation = "DRIFTING" if not critical_fail else "DOCTRINE_VIOLATION"
    elif any_warn and max_escalation == "ALIGNED":
        max_escalation = "PARTIALLY_ALIGNED"

    verdict = max_escalation
    return {
        "verdict": verdict,
        "dimensions": {k: dim_level[k] for k in DOCTRINE_DIMENSIONS},
        "triggered_rules": triggered,
        "explanation": explanations,
        "signals_evaluated": sorted(signals),
    }

