"""
Canonical system mission — single import for CEO, bots, orchestration, daemon, reviews.

Targets (e.g. trajectory toward high daily ROI) are **scored and pursued**, never asserted as guaranteed outcomes.
All live execution remains fail-closed behind the existing gate stack.
"""

from __future__ import annotations

from typing import Any, Dict, Final, List

# Permanent operating philosophy (human + machine-readable).
SYSTEM_OPERATING_PHILOSOPHY: Final[str] = (
    "Discover, validate, and exploit the fastest repeatable paths to extreme upside through autonomous "
    "research, testing, review, implementation, and adaptation — while preserving truthfulness, capital "
    "discipline, and strict fail-closed safety."
)

# Primary measurable objective: maximize risk-adjusted, evidence-backed upside velocity (not a guaranteed rate).
PRIMARY_SYSTEM_OBJECTIVE: Final[str] = (
    "Find and scale the fastest repeatable profitable edges across avenues, gates, strategies, latency patterns, "
    "entry/exit methods, timing patterns, and execution improvements — using only measured evidence."
)

SECONDARY_OBJECTIVES: Final[List[str]] = [
    "compress_learning_time",
    "compress_time_to_edge",
    "compress_time_to_promotion",
    "compress_time_to_capital_readiness",
    "compress_time_to_repeatable_profitable_deployment",
    "reduce_wasteful_research_and_token_burn",
    "reduce_slow_or_non_productive_strategies",
    "aggressively_reallocate_effort_toward_what_is_working",
    "minimize_losses_and_drawdowns_subject_to_safety_gates",
    "maximize_trade_efficiency_profit_per_unit_time_where_measured",
    "reduce_latency_between_opportunity_detection_and_execution_where_safe",
    "eliminate_unproductive_strategies_quickly_via_demotion_disable",
]

TRUTH_PRINCIPLE: Final[str] = (
    "Never hallucinate edge, profitability, or readiness. Ground decisions in measured evidence: artifacts, "
    "replay results, smoke results, real ledger results, or explicit truth files."
)

AUTOMATION_PRINCIPLE: Final[str] = (
    "Automatically research, rank candidates, spawn scoped bots within caps, review, audit, queue implementation, "
    "promote or demote paths, run CEO sessions, and maintain cumulative memory — operator enables supervised live "
    "and only promotes autonomous paths through objective contracts."
)

# Aspirational trajectory framing (scoring horizon — NOT a promise of returns).
ASPIRATIONAL_ROI_TRAJECTORY_NOTE: Final[str] = (
    "The system scores all strategies and edges by how quickly measured results could compound capital toward "
    "aggressive upside targets (e.g. high daily ROI as a horizon). This is an optimization pressure toward "
    "evidence-backed repeatability — it does not promise any fixed rate of return."
)

LEARNING_PHASES: Final[Dict[str, str]] = {
    "phase_1": "Days 1–3: exploration, data gathering, edge discovery (shadow / supervised as allowed).",
    "phase_2": "After ~100–150 trades: emphasize convergence and ranking from measured outcomes.",
    "phase_3": "Days 4–6 onward: prioritize execution of highest-scoring strategies **only** when gates and truth allow.",
}

MISSION_VERSION: Final[str] = "system_mission_v2"


def system_mission_dict() -> Dict[str, Any]:
    """Machine-readable mission blob for prompts, registries, and artifacts."""
    return {
        "truth_version": MISSION_VERSION,
        "operating_philosophy": SYSTEM_OPERATING_PHILOSOPHY,
        "primary_objective": PRIMARY_SYSTEM_OBJECTIVE,
        "secondary_objectives": list(SECONDARY_OBJECTIVES),
        "truth_principle": TRUTH_PRINCIPLE,
        "automation_principle": AUTOMATION_PRINCIPLE,
        "aspirational_trajectory_note": ASPIRATIONAL_ROI_TRAJECTORY_NOTE,
        "learning_phases": dict(LEARNING_PHASES),
        "learning_mandate": (
            "Every completed trade is data: what worked, what failed, why, and what to try next — stored structurally."
        ),
        "behavior": [
            "adaptive",
            "aggressive_but_risk_gated",
            "continuously_optimizing",
            "avoid_idle_research_loops_when_budgets_allow_actionable_queue_items",
            "keep_capital_working_when_safe_per_governor_and_truth",
        ],
        "mission_stage_labeling": {
            "label_kind": "rule_based_phase_metadata",
            "not_ml": True,
            "honesty": "Learning phases and mission labels are policy text — not a learned stage classifier.",
        },
    }


def default_bot_mission_fields() -> Dict[str, Any]:
    """Canonical per-bot mission fields (merged into registry records)."""
    sm = system_mission_dict()
    return {
        "core_mission": sm["operating_philosophy"],
        "optimization_objective": sm["primary_objective"],
        "sub_objectives": list(sm["secondary_objectives"]),
        "profitability_focus": "maximize_measured_risk_adjusted_edge_and_compounding_velocity",
        "improvement_focus": "minimize_time_to_evidence_backed_profitable_convergence",
        "token_efficiency_focus": "minimize_tokens_per_unit_of_validated_truth_and_ranked_action",
        "live_safety_obligation": "fail_closed_no_bypass_of_gate_stack_or_execution_authority",
        "truth_obligation": sm["truth_principle"],
    }


def mission_prompt_injection_block() -> str:
    """Short block for CEO / review prompts (deterministic)."""
    sm = system_mission_dict()
    lines = [
        sm["operating_philosophy"],
        "",
        f"Primary: {sm['primary_objective']}",
        sm["aspirational_trajectory_note"],
        "",
        f"Truth: {sm['truth_principle']}",
    ]
    return "\n".join(lines)
