"""Scenario catalog — same ids run across avenues/gates via harness (honest wiring gates)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from trading_ai.daemon_testing.contract import ScenarioDef

# Trade lifecycle + failure + restart (Section 1.B)
ALL_SCENARIOS: List[ScenarioDef] = [
    ScenarioDef("no_candidate", "No candidate opportunity", "lifecycle"),
    ScenarioDef("candidate_blocked_pretrade", "Candidate blocked at pre-trade gate", "lifecycle"),
    ScenarioDef("duplicate_blocked", "Duplicate guard blocked duplicate submit", "lifecycle"),
    ScenarioDef("entry_rejected_before_fill", "Entry rejected before any fill", "lifecycle"),
    ScenarioDef("entry_filled_exit_rejected", "Entry filled / exit rejected", "lifecycle"),
    ScenarioDef("entry_filled_exit_partially_failed", "Entry filled / exit partially failed", "lifecycle"),
    ScenarioDef("full_buy_sell_log", "Full buy→sell→log path", "lifecycle"),
    ScenarioDef("full_buy_sell_log_rebuy_eligible", "Full path; rebuy eligible after terminal truth", "lifecycle"),
    ScenarioDef("rebuy_blocked_by_policy", "Rebuy blocked by policy", "lifecycle"),
    ScenarioDef("rebuy_blocked_by_lessons", "Rebuy blocked by lessons", "lifecycle"),
    ScenarioDef("rebuy_blocked_by_adaptive", "Rebuy blocked by adaptive brake", "lifecycle"),
    ScenarioDef("rebuy_blocked_by_logging_incomplete", "Rebuy blocked when logging incomplete", "lifecycle"),
    ScenarioDef("remote_sync_fail_after_local_success", "Remote sync failed after local success", "lifecycle"),
    ScenarioDef("governance_fail", "Governance denial mid-path", "lifecycle"),
    ScenarioDef("review_update_fail", "Review packet / review update failure", "lifecycle"),
    ScenarioDef("daemon_restart_inflight", "Daemon restart after in-flight state", "restart"),
    ScenarioDef("daemon_restart_finalized", "Daemon restart after finalized state", "restart"),
    # Failure injection (Section 5) — also listed in failure_injection_truth
    ScenarioDef("fi_duplicate_guard_collision", "Failure: duplicate guard collision", "failure_injection"),
    ScenarioDef("fi_local_databank_write_exception", "Failure: local databank write exception", "failure_injection"),
    ScenarioDef("fi_remote_sync_timeout", "Failure: remote sync timeout", "failure_injection"),
    ScenarioDef("fi_governance_denial", "Failure: governance denial", "failure_injection"),
    ScenarioDef("fi_review_packet_failure", "Failure: review packet failure", "failure_injection"),
    ScenarioDef("fi_adaptive_emergency_brake", "Failure: adaptive emergency brake", "failure_injection"),
    ScenarioDef("fi_kill_switch_trip", "Failure: kill switch trip", "failure_injection"),
    ScenarioDef("fi_authoritative_halt_flip", "Failure: authoritative halt flips during loop", "failure_injection"),
    ScenarioDef("fi_runtime_root_mismatch", "Failure: runtime root mismatch mid-run", "failure_injection"),
    ScenarioDef("fi_env_fingerprint_mismatch", "Failure: env fingerprint mismatch mid-run", "failure_injection"),
    ScenarioDef("fi_stale_artifact_before_live", "Failure: stale artifact set before live step", "failure_injection"),
    ScenarioDef("fi_malformed_trade_record", "Failure: adapter returned malformed trade record", "failure_injection"),
    ScenarioDef("fi_capability_lie", "Failure: adapter misreports capability", "failure_injection"),
    ScenarioDef("fi_partial_fill_no_exit_truth", "Failure: partial fill with no exit truth", "failure_injection"),
    ScenarioDef("fi_order_reject_storm", "Failure: repeated order reject storm", "failure_injection"),
    ScenarioDef("fi_lock_contention", "Failure: lock contention / second daemon start", "failure_injection"),
]


def scenario_by_id(sid: str) -> Optional[ScenarioDef]:
    for s in ALL_SCENARIOS:
        if s.scenario_id == sid:
            return s
    return None


def fake_outcome_template(scenario_id: str) -> Dict[str, Any]:
    """
    Deterministic fake semantics for exhaustive logic tests — NOT live proof.
    Keys align with DaemonMatrixRow construction in fake_adapters.
    """
    z = {
        "has_candidate": True,
        "pretrade_block": False,
        "duplicate_block": False,
        "entry_submit_ok": True,
        "entry_fill": False,
        "exit_submit_ok": True,
        "exit_fill": False,
        "partial_exit": False,
        "pnl_ok": False,
        "local_ok": False,
        "remote_ok": True,
        "gov_ok": True,
        "review_ok": True,
        "rebuy_policy_ok": True,
        "lessons_block": False,
        "adaptive_block": False,
        "logging_complete": True,
        "abort": False,
        "abort_reason": "",
        "inflight_at_restart": False,
        "finalized_at_restart": False,
    }
    if scenario_id == "no_candidate":
        z.update(has_candidate=False)
    elif scenario_id == "candidate_blocked_pretrade":
        z.update(pretrade_block=True)
    elif scenario_id == "duplicate_blocked":
        z.update(duplicate_block=True)
    elif scenario_id == "entry_rejected_before_fill":
        z.update(entry_submit_ok=False)
    elif scenario_id == "entry_filled_exit_rejected":
        z.update(entry_fill=True, exit_submit_ok=False)
    elif scenario_id == "entry_filled_exit_partially_failed":
        z.update(entry_fill=True, partial_exit=True, exit_fill=True, pnl_ok=False, local_ok=True)
    elif scenario_id == "full_buy_sell_log":
        z.update(entry_fill=True, exit_fill=True, pnl_ok=True, local_ok=True)
    elif scenario_id == "full_buy_sell_log_rebuy_eligible":
        z.update(
            entry_fill=True,
            exit_fill=True,
            pnl_ok=True,
            local_ok=True,
            logging_complete=True,
            rebuy_policy_ok=True,
        )
    elif scenario_id == "rebuy_blocked_by_policy":
        z.update(
            entry_fill=True,
            exit_fill=True,
            pnl_ok=True,
            local_ok=True,
            rebuy_policy_ok=False,
        )
    elif scenario_id == "rebuy_blocked_by_lessons":
        z.update(entry_fill=True, exit_fill=True, pnl_ok=True, local_ok=True, lessons_block=True)
    elif scenario_id == "rebuy_blocked_by_adaptive":
        z.update(entry_fill=True, exit_fill=True, pnl_ok=True, local_ok=True, adaptive_block=True)
    elif scenario_id == "rebuy_blocked_by_logging_incomplete":
        z.update(entry_fill=True, exit_fill=True, logging_complete=False, local_ok=False)
    elif scenario_id == "remote_sync_fail_after_local_success":
        z.update(entry_fill=True, exit_fill=True, pnl_ok=True, local_ok=True, remote_ok=False)
    elif scenario_id == "governance_fail":
        z.update(entry_fill=True, gov_ok=False)
    elif scenario_id == "review_update_fail":
        z.update(entry_fill=True, exit_fill=True, review_ok=False)
    elif scenario_id == "daemon_restart_inflight":
        z.update(entry_fill=True, exit_fill=False, inflight_at_restart=True)
    elif scenario_id == "daemon_restart_finalized":
        z.update(entry_fill=True, exit_fill=True, pnl_ok=True, local_ok=True, finalized_at_restart=True)
    elif scenario_id.startswith("fi_"):
        return _fake_failure_template(scenario_id, z)
    return z


def _fake_failure_template(sid: str, z: Dict[str, Any]) -> Dict[str, Any]:
    z = dict(z)
    z["fi"] = True
    if sid == "fi_duplicate_guard_collision":
        z.update(duplicate_block=True)
    elif sid == "fi_local_databank_write_exception":
        z.update(entry_fill=True, local_ok=False)
    elif sid == "fi_remote_sync_timeout":
        z.update(entry_fill=True, exit_fill=True, remote_ok=False)
    elif sid == "fi_governance_denial":
        z.update(entry_fill=True, gov_ok=False)
    elif sid == "fi_review_packet_failure":
        z.update(entry_fill=True, exit_fill=True, review_ok=False)
    elif sid == "fi_adaptive_emergency_brake":
        z.update(abort=True, abort_reason="emergency_brake", adaptive_block=True)
    elif sid == "fi_kill_switch_trip":
        z.update(abort=True, abort_reason="kill_switch")
    elif sid == "fi_authoritative_halt_flip":
        z.update(abort=True, abort_reason="global_halt_authoritative")
    elif sid == "fi_runtime_root_mismatch":
        z.update(abort=True, abort_reason="runtime_root_mismatch")
    elif sid == "fi_env_fingerprint_mismatch":
        z.update(abort=True, abort_reason="env_fingerprint_mismatch")
    elif sid == "fi_stale_artifact_before_live":
        z.update(abort=True, abort_reason="stale_artifacts")
    elif sid == "fi_malformed_trade_record":
        z.update(entry_fill=True, malformed_record=True)
    elif sid == "fi_capability_lie":
        z.update(capability_lie=True)
    elif sid == "fi_partial_fill_no_exit_truth":
        z.update(entry_fill=True, partial_exit=True, exit_fill=False)
    elif sid == "fi_order_reject_storm":
        z.update(entry_submit_ok=False)
    elif sid == "fi_lock_contention":
        z.update(lock_contention=True)
    return z
