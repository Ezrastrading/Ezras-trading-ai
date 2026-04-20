"""
Operator-facing autonomous path summary — authoritative sources, deduped blockers, next steps.

Does not enable live trading; merges artifact reads + normalization only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.orchestration.autonomous_blocker_normalization import (
    extract_historical_from_last_failure_json,
    normalize_autonomous_blockers,
)
from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import compute_autonomous_live_runtime_proven_tuple
from trading_ai.orchestration.avenue_a_daemon_policy import (
    avenue_a_autonomous_live_allowed,
    min_consecutive_autonomous_cycles_required,
)
from trading_ai.orchestration.autonomous_daemon_live_contract import autonomous_daemon_may_submit_live_orders
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def build_autonomous_operator_path(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)

    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    last_fail = ad.read_json("data/control/runtime_runner_last_failure.json")
    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}

    cons_ok = bool(cons.get("consistent_with_authoritative_artifacts"))
    aut_allowed_now = bool(auth.get("avenue_a_can_run_autonomous_live_now"))

    proven_tuple = compute_autonomous_live_runtime_proven_tuple(runtime_root=root)
    raw_from_tuple = list(proven_tuple[1])

    # Section-1 style duplicates may also live in exact_blockers_autonomous on authority
    raw_authority = list(auth.get("exact_blockers_autonomous") or [])
    merged_raw = list(dict.fromkeys(raw_authority + raw_from_tuple))

    historical_inputs = extract_historical_from_last_failure_json(
        last_fail if isinstance(last_fail, dict) else None
    )

    norm = normalize_autonomous_blockers(
        raw_blocker_inputs=merged_raw,
        runtime_consistency_green=cons_ok,
        historical_raw_inputs=historical_inputs,
    )

    live_ok, dual_bl = autonomous_daemon_may_submit_live_orders(runtime_root=root)
    path_ok, path_why = avenue_a_autonomous_live_allowed(runtime_root=root)

    n_need = min_consecutive_autonomous_cycles_required()
    n_have = int(st.get("consecutive_autonomous_live_only_ok_cycles") or 0)
    n_supervised_mixed = int(st.get("consecutive_autonomous_ok_cycles") or 0)

    halt_primary = str(gh.get("global_halt_primary_classification") or "")
    halt_auth = bool(gh.get("global_halt_is_currently_authoritative"))
    stale = bool(gh.get("global_halt_is_stale"))
    auth_halt_audit = auth.get("autonomous_halt_audit") if isinstance(auth.get("autonomous_halt_audit"), dict) else {}

    current_halt_source = "data/control/gate_b_global_halt_truth.json"
    stale_sources: List[str] = []
    if stale:
        stale_sources.append("gate_b_global_halt_truth:global_halt_is_stale_true")

    why_one = ""
    if not path_ok:
        why_one = str(path_why)[:500]
    elif not aut_allowed_now:
        why_one = "daemon_live_switch_authority.avenue_a_can_run_autonomous_live_now is false — see active_blockers"
    elif not live_ok:
        why_one = "dual gate for venue orders false — " + ";".join(dual_bl[:6])
    else:
        why_one = "autonomous policy path may be clear under authority — still requires venue dual gate + non-stale halt for live"

    blocker_table = []
    for i, b in enumerate(norm.get("active_blockers") or [], start=1):
        blocker_table.append({"rank": i, "blocker": b, "class": "active"})

    why_not_arm: List[str] = []
    if not cons_ok:
        why_not_arm.append("runtime_env_not_consistent_with_daemon_live_switch_authority")
    if not aut_allowed_now:
        why_not_arm.extend(list(auth.get("exact_blockers_autonomous") or [])[:12])
    if not proven_tuple[0]:
        why_not_arm.extend(list(proven_tuple[1])[:20])
    if stale:
        why_not_arm.append("stale_global_halt_classification_autonomous_forbidden")

    next_steps: List[str] = []
    if not cons_ok:
        next_steps.append("Run: python -m trading_ai.deployment refresh-supervised-daemon-truth-chain from the same shell as EZRAS_RUNTIME_ROOT")
    if stale:
        next_steps.append("Refresh gate_b_global_halt_truth — stale classification forbids autonomous")
    if norm.get("consecutive_cycle_progress"):
        cp = norm["consecutive_cycle_progress"]
        next_steps.append(
            f"Complete {cp.get('remaining', 0)} more successful autonomous_live-only daemon cycles "
            f"(need {cp.get('required')}, have {cp.get('current')})"
        )

    already_satisfied: List[str] = []
    still_missing: List[str] = []
    if cons_ok:
        already_satisfied.append("daemon_runtime_consistency_truth.consistent_with_authoritative_artifacts")
    else:
        still_missing.append("daemon_runtime_consistency_truth_not_green")
    if proven_tuple[0]:
        already_satisfied.append("autonomous_live_runtime_proven_tuple")
    else:
        still_missing.append("autonomous_live_runtime_not_proven")
    if path_ok:
        already_satisfied.append("avenue_a_autonomous_live_allowed")
    else:
        still_missing.append("avenue_a_autonomous_live_not_allowed")
    if aut_allowed_now:
        already_satisfied.append("daemon_live_switch_authority.avenue_a_can_run_autonomous_live_now")
    else:
        still_missing.append("daemon_live_switch_authority_blocks_autonomous")
    if live_ok:
        already_satisfied.append("dual_gate_allows_venue_submission")
    else:
        still_missing.append("dual_gate_blocks_venue_submission")
    if not stale and halt_auth:
        already_satisfied.append("global_halt_authoritative_and_non_stale_for_policy")
    elif stale:
        still_missing.append("stale_global_halt_classification_autonomous_forbidden")
    elif not halt_auth:
        still_missing.append("global_halt_not_authoritative_or_unknown")

    bundle = ad.read_json("data/control/autonomous_verification_proof_bundle.json") or {}
    ver_sum = {
        "daemon_context_loop_proof": "data/control/daemon_context_loop_proof.json",
        "failure_stop_runtime_proof": "data/control/daemon_failure_stop_runtime_proof.json",
        "lock_exclusivity_runtime_proof": "data/control/daemon_lock_exclusivity_runtime_proof.json",
        "bundle_all_green": bool(bundle.get("all_runtime_components_verified")),
        "bundle_truth_version": bundle.get("truth_version"),
    }

    return {
        "truth_version": "autonomous_operator_path_v2",
        "runtime_root": str(root),
        "current_authority_sources": {
            "daemon_live_switch_authority": "data/control/daemon_live_switch_authority.json",
            "daemon_runtime_consistency_truth": "data/control/daemon_runtime_consistency_truth.json",
            "gate_b_global_halt_truth": current_halt_source,
            "runtime_runner_last_failure": "data/control/runtime_runner_last_failure.json (historical hints only)",
            "autonomous_verification_proof_bundle": "data/control/autonomous_verification_proof_bundle.json",
        },
        "current_autonomous_readiness_blockers": norm.get("active_blockers"),
        "historical_autonomous_artifact_notes": norm.get("historical_or_stale_blockers"),
        "halt_audit": {
            "current_halt_source_path": str(root / current_halt_source),
            "current_halt_decision": {
                "primary_classification": halt_primary,
                "global_halt_is_currently_authoritative": halt_auth,
                "global_halt_is_stale_persisted": stale,
            },
            "stale_halt_sources_detected": stale_sources,
            "exact_rule_why_autonomous_blocked_if_halt": (
                "Autonomous requires non-stale, authoritative halt classification with governance clear — "
                "see daemon_live_switch_authority.global_halt_daemon_section_c and gate_b_global_halt_truth."
            ),
        },
        "autonomous_halt_audit": auth_halt_audit,
        "active_blockers": norm.get("active_blockers"),
        "historical_notes": norm.get("historical_or_stale_blockers"),
        "autonomous_proof_progress": {
            "autonomous_live_runtime_proven": bool(proven_tuple[0]),
            "min_consecutive_autonomous_cycles_required": n_need,
            "observed_consecutive_autonomous_live_only_ok_cycles": n_have,
            "supervised_mixed_cycle_counter_not_used_for_autonomous": n_supervised_mixed,
            "consecutive_cycle": norm.get("consecutive_cycle_progress"),
            "last_counted_cycle_ts": st.get("last_counted_autonomous_cycle_ts"),
            "last_counted_trade_id": st.get("last_counted_autonomous_trade_id"),
            "last_cycle_counted_reason": st.get("last_autonomous_cycle_counted_reason"),
            "last_cycle_not_counted_reason": st.get("last_autonomous_cycle_count_reset_reason"),
        },
        "proof_progress": {
            "autonomous_live_runtime_proven": bool(proven_tuple[0]),
            "min_consecutive_autonomous_cycles_required": n_need,
            "observed_consecutive_autonomous_live_only_ok_cycles": n_have,
            "consecutive_cycle": norm.get("consecutive_cycle_progress"),
        },
        "autonomous_verification_summary": ver_sum,
        "exact_next_runtime_steps": next_steps,
        "exact_next_steps_if_false": next_steps,
        "can_arm_autonomous_now": bool(path_ok and aut_allowed_now and cons_ok),
        "why_not_armable_now": why_not_arm[:30] if why_not_arm else "",
        "can_submit_live_orders_under_dual_gate": bool(live_ok),
        "dual_gate_blockers_if_false": dual_bl,
        "why_not_in_one_sentence": why_one,
        "blocker_table": blocker_table,
        "raw_autonomous_reason_chain": norm.get("raw_autonomous_reason_chain"),
        "raw_autonomous_blocker_chain_debug": norm.get("raw_autonomous_reason_chain"),
        "autonomous_blocker_debug": norm.get("autonomous_blocker_debug"),
        "deduped_blocker_chain_string": norm.get("deduped_blocker_chain_string"),
        "operator_blocker_domain_groups_v2": norm.get("operator_domain_groups_v2"),
        "progression": {
            "already_satisfied": already_satisfied,
            "still_missing": still_missing,
            "what_must_happen_next": next_steps,
        },
        "honesty": (
            "can_arm_autonomous_now reflects avenue_a_autonomous_live_allowed + consistency green; "
            "venue orders still require autonomous_daemon_live_enable + EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED."
        ),
    }
