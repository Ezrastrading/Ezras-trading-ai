"""System law — immutable priority order and forbidden behaviors (import-time constants)."""

from __future__ import annotations

from typing import Any, Dict, List

# 1 = highest priority. Used by schedulers, incident response, and CEO aggregation.
OBJECTIVE_HIERARCHY: List[str] = [
    "capital_protection",
    "truth_integrity",
    "execution_cleanliness",
    "stable_edge",
    "profitable_scaling",
    "speed",
    "research_experimentation",
]

SYSTEM_CONSTITUTION: Dict[str, Any] = {
    "truth_version": "system_constitution_v1",
    "purpose": "Supervised multi-venue trading support with earned authority and auditable automation.",
    "non_negotiable": [
        "Intelligence is automatic; live authority is earned and constrained.",
        "No component may escalate its own live power without deterministic proof.",
        "Single execution authority per route must always be enforced.",
        "Capital protection overrides optimization.",
        "Promotion tier, capital tier, and execution authority are distinct channels.",
        "System must degrade safely under all failure modes.",
    ],
    "forbidden": [
        "Unrestricted autonomous venue order placement",
        "Self-modifying live gate logic without quarantine pipeline",
        "Bypass of portfolio risk or capital governor when enforcement is enabled",
        "Silent failure states for material control paths",
    ],
    "requires_hard_approval": [
        "Strategy/gate threshold changes affecting live routing",
        "Capital tier escalation beyond policy envelope",
        "Promotion past bounded-live execution rung",
        "Cross-avenue capital reallocation beyond automated band",
    ],
    "objective_hierarchy": OBJECTIVE_HIERARCHY,
    "conflict_resolution": "Lower-index objectives in OBJECTIVE_HIERARCHY always win; CEO review artifact is tie-breaker for policy ambiguity.",
    "final_truth_authority": "Designated system truth writers per domain (see lock_layer.truth_writers); bots may only propose.",
}
