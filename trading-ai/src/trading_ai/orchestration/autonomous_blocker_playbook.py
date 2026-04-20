"""
Machine-resolvable mapping from autonomous blocker tokens to source artifacts and clear commands.

Fail-closed: unmatched blockers still appear with playbook_match=false (never dropped).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

# Ordered: first match wins per blocker string (longer / more specific patterns should appear first).
_PLAYBOOK: Tuple[Dict[str, Any], ...] = (
    {
        "id": "insufficient_consecutive_autonomous_cycles",
        "match": re.compile(
            r"^insufficient_consecutive_autonomous_live_ok_cycles_need_(\d+)_have_(\d+)$"
        ),
        "primary_artifacts": ["data/control/avenue_a_daemon_state.json"],
        "truth_fields": [
            "consecutive_autonomous_live_only_ok_cycles",
            "last_counted_autonomous_cycle_ts",
            "last_autonomous_cycle_counted_reason",
        ],
        "clear_commands": [
            "export EZRAS_RUNTIME_ROOT=<same_as_daemon> EZRAS_AVENUE_A_DAEMON_MODE=autonomous_live",
            "python -m trading_ai.deployment avenue-a-daemon-once --quote-usd 10 --product-id BTC-USD",
            "python -m trading_ai.deployment avenue-a-daemon-status",
        ],
        "what_clears": (
            "Successful autonomous_live-only cycles that satisfy avenue_a_daemon_policy increment "
            "consecutive_autonomous_live_only_ok_cycles until it reaches the required minimum."
        ),
    },
    {
        "id": "continuous_daemon_verification_flags_incomplete",
        "substrings": ("continuous_daemon_verification_flags_incomplete",),
        "primary_artifacts": ["data/control/runtime_runner_daemon_verification.json"],
        "truth_fields": [
            "continuous_daemon_verification_ok",
            "daemon_verification_matrix_complete",
            "verification_source",
        ],
        "clear_commands": [
            "python -m trading_ai.deployment write-daemon-readiness",
            "python -m trading_ai.deployment run-daemon-test-matrix --levels full",
            "python -m trading_ai.deployment autonomous-verification-smoke",
        ],
        "what_clears": (
            "Runtime runner verification JSON must show complete daemon verification flags from a non-test harness "
            "when policy requires it; refresh daemon readiness and re-run verification smoke."
        ),
    },
    {
        "id": "daemon_context_loop_not_proven",
        "substrings": ("daemon_context_loop_not_proven",),
        "primary_artifacts": [
            "data/control/daemon_context_loop_proof.json",
            "data/control/universal_execution_loop_proof.json",
            "data/control/avenue_a_daemon_loop_emit_stamp.json",
        ],
        "truth_fields": ["daemon_context_loop_proven", "trade_id_loop", "trade_id_stamp", "env_fingerprint_matches"],
        "clear_commands": [
            "python -m trading_ai.deployment avenue-a-daemon-once  # emits loop stamp when cycle proves",
            "python -m trading_ai.deployment autonomous-verification-smoke",
        ],
        "what_clears": (
            "Emit stamp trade_id must match universal loop last_trade_id with matching runtime root and env fingerprint."
        ),
    },
    {
        "id": "failure_stop_runtime",
        "substrings": (
            "failure_stop_verified_not_true",
            "failure_stop_not_runtime_verified",
            "runtime_runner_daemon_verification.failure_stop_verified_not_true",
        ),
        "primary_artifacts": [
            "data/control/runtime_runner_daemon_verification.json",
            "data/control/daemon_failure_stop_runtime_proof.json",
        ],
        "truth_fields": [
            "failure_stop_verified",
            "verification_source",
            "runtime_observed_failure_stop_verified",
        ],
        "clear_commands": [
            "python -m trading_ai.deployment autonomous-failure-stop-verification-smoke",
            "python -m trading_ai.deployment run-daemon-test-matrix  # non-unit-test verification when applicable",
        ],
        "what_clears": (
            "failure_stop_verified must be true with verification_source not equal to unit_test_harness for runtime proof."
        ),
    },
    {
        "id": "lock_exclusivity_runtime",
        "substrings": (
            "lock_exclusivity_verified_not_true",
            "lock_exclusivity_not_runtime_verified",
            "runtime_runner_daemon_verification.lock_exclusivity_verified_not_true",
        ),
        "primary_artifacts": [
            "data/control/runtime_runner_daemon_verification.json",
            "data/control/daemon_lock_exclusivity_runtime_proof.json",
        ],
        "truth_fields": [
            "lock_exclusivity_verified",
            "verification_source",
            "runtime_observed_lock_exclusivity_verified",
        ],
        "clear_commands": [
            "python -m trading_ai.deployment autonomous-lock-exclusivity-verification-smoke",
            "python -m trading_ai.deployment daemon-stop  # clear stale lock if safe",
        ],
        "what_clears": (
            "Lock exclusivity verified true on runtime verification artifact; not satisfied by unit-test-only proofs."
        ),
    },
    {
        "id": "stale_global_halt",
        "substrings": ("stale_global_halt_classification_autonomous_forbidden",),
        "primary_artifacts": ["data/control/gate_b_global_halt_truth.json"],
        "truth_fields": ["global_halt_is_stale", "global_halt_is_currently_authoritative", "global_halt_primary_classification"],
        "clear_commands": [
            "python -m trading_ai.deployment refresh-runtime-artifacts",
            "python -m trading_ai.deployment gate-b-tick  # when Gate B owns halt refresh in your ops runbook",
        ],
        "what_clears": (
            "Refresh authoritative global halt truth so global_halt_is_stale is false under governance policy."
        ),
    },
    {
        "id": "authoritative_global_halt_blocks_autonomous",
        "substrings": ("authoritative_global_halt_blocks_autonomous",),
        "primary_artifacts": [
            "data/control/gate_b_global_halt_truth.json",
            "data/control/daemon_live_switch_authority.json",
        ],
        "truth_fields": [
            "global_halt_is_currently_authoritative",
            "global_halt_primary_classification",
            "autonomous_halt_audit",
        ],
        "clear_commands": [
            "Inspect data/control/gate_b_global_halt_truth.json and org governance process",
            "python -m trading_ai.deployment avenue-a-go-live-verdict",
        ],
        "what_clears": (
            "Resolve governance halt condition; autonomous is forbidden while authoritative halt blocks it."
        ),
    },
    {
        "id": "runtime_consistency_env_root",
        "substrings": (
            "runtime_root_or_env_fingerprint_mismatch",
            "daemon_runtime_consistency_truth_not_green",
            "runtime_env_not_consistent_with_daemon_live_switch_authority",
        ),
        "primary_artifacts": [
            "data/control/daemon_runtime_consistency_truth.json",
            "data/control/daemon_live_switch_authority.json",
        ],
        "truth_fields": [
            "consistent_with_authoritative_artifacts",
            "runtime_env_fingerprint",
            "authority_env_fingerprint",
        ],
        "clear_commands": [
            "python -m trading_ai.deployment refresh-supervised-daemon-truth-chain",
        ],
        "what_clears": (
            "Shell EZRAS_RUNTIME_ROOT and env fingerprint must match daemon_live_switch_authority stamps."
        ),
    },
    {
        "id": "autonomous_live_enable_artifact",
        "substrings": (
            "autonomous_daemon_live_enable",
            "dual_gate",
            "EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED",
        ),
        "primary_artifacts": [
            "data/control/autonomous_daemon_live_enable.json",
        ],
        "truth_fields": ["confirmed", "avenue_ids", "gate_ids"],
        "clear_commands": [
            "python -m trading_ai.deployment daemon-arm-live --confirm --operator <id>",
            "export EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true  # process env; only after policy clear",
        ],
        "what_clears": (
            "Explicit arm file confirmed true plus process env for autonomous live; separate from supervised."
        ),
    },
)


def resolve_playbook_entries_for_blocker(blocker: str) -> List[Dict[str, Any]]:
    """Return playbook row dicts (with match metadata) for one blocker string."""
    s = str(blocker or "").strip()
    if not s:
        return []
    out: List[Dict[str, Any]] = []
    for spec in _PLAYBOOK:
        matched = False
        kind = "unmatched"
        groups: Dict[str, Any] = {}
        rx = spec.get("match")
        if isinstance(rx, re.Pattern):
            m = rx.match(s)
            if m:
                matched = True
                kind = "regex"
                groups = {"need": m.group(1), "have": m.group(2)}
        if not matched:
            for sub in spec.get("substrings") or ():
                if sub in s:
                    matched = True
                    kind = "substring"
                    break
        if matched:
            row = {
                "playbook_id": spec["id"],
                "match_kind": kind,
                "playbook_match": True,
                "blocker": s,
                "primary_artifacts": list(spec.get("primary_artifacts") or []),
                "truth_fields": list(spec.get("truth_fields") or []),
                "clear_commands": list(spec.get("clear_commands") or []),
                "what_must_happen_to_clear": spec.get("what_clears", ""),
            }
            if groups:
                row["parsed_groups"] = groups
            out.append(row)
            break
    if not out:
        out.append(
            {
                "playbook_id": "unmapped_blocker",
                "match_kind": "none",
                "playbook_match": False,
                "blocker": s,
                "primary_artifacts": ["data/control/daemon_live_switch_authority.json"],
                "truth_fields": ["exact_blockers_autonomous", "avenue_a_can_run_autonomous_live_now"],
                "clear_commands": [
                    "python -m trading_ai.deployment autonomous-proof-report",
                    "python -m trading_ai.deployment avenue-a-go-live-verdict",
                ],
                "what_must_happen_to_clear": (
                    "Inspect authoritative daemon_live_switch_authority and related control artifacts; "
                    "no playbook mapping — treat as policy-specific."
                ),
            }
        )
    return out


def enrich_active_blockers_with_playbook(active_blockers: Sequence[str]) -> List[Dict[str, Any]]:
    """One consolidated row per unique blocker, with playbook resolution."""
    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    for b in active_blockers:
        key = str(b or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        resolved = resolve_playbook_entries_for_blocker(key)
        rows.append({"blocker": key, "playbook_resolution": resolved})
    return rows
