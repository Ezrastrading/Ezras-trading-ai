"""Autonomous blocker normalization + consecutive cycle parse + stale mismatch when consistency green."""

from __future__ import annotations

from trading_ai.orchestration.autonomous_blocker_normalization import (
    normalize_autonomous_blockers,
    parse_consecutive_cycle_blocker,
)


def test_dedup_repeated_daemon_authority_semantics() -> None:
    raw = [
        "daemon_authority:block_a",
        "daemon_authority:block_a",
        "lock_exclusivity_not_runtime_verified",
        "runtime_runner_daemon_verification.lock_exclusivity_verified_not_true",
    ]
    n = normalize_autonomous_blockers(raw_blocker_inputs=raw, runtime_consistency_green=False)
    assert len(n["active_blockers"]) < len(raw)


def test_stale_fingerprint_historical_when_consistency_green() -> None:
    raw = ["runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority", "daemon_context_loop_not_proven"]
    n = normalize_autonomous_blockers(raw_blocker_inputs=raw, runtime_consistency_green=True)
    assert "daemon_context_loop_not_proven" in n["active_blockers"]
    assert any("mismatch" in x for x in n["historical_or_stale_blockers"])


def test_consecutive_cycle_structured() -> None:
    p = parse_consecutive_cycle_blocker("insufficient_consecutive_autonomous_live_ok_cycles_need_5_have_2")
    assert p is not None
    assert p["required"] == 5
    assert p["current"] == 2
    assert p["remaining"] == 3


def test_umbrella_daemon_verification_suppressed_when_atomic_present() -> None:
    raw = ["daemon_verification_incomplete", "failure_stop_not_runtime_verified"]
    n = normalize_autonomous_blockers(raw_blocker_inputs=raw, runtime_consistency_green=False)
    assert "failure_stop_verified_not_true" in n["active_blockers"]
    assert "daemon_verification_incomplete" not in n["active_blockers"]
