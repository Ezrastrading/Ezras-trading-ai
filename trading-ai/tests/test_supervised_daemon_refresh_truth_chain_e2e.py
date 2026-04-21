"""
End-to-end supervised truth chain: stale authority fingerprint vs current shell, then canonical refresh.

Proves :func:`refresh_supervised_daemon_truth_chain` (same path as ``refresh-supervised-daemon-truth-chain`` CLI)
clears ``daemon_runtime_consistency:daemon_runtime_consistency:runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority``
when the process env matches after re-stamp and other supervised prerequisites are satisfied.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.orchestration.supervised_avenue_a_truth import (
    append_supervised_trade_log_line,
    refresh_supervised_daemon_truth_chain,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _minimal_switch_a(rt: Path) -> None:
    from trading_ai.control.system_execution_lock import save_system_execution_lock

    (rt / "data/control").mkdir(parents=True, exist_ok=True)
    save_system_execution_lock(
        {
            "system_locked": True,
            "ready_for_live_execution": True,
            "gate_a_enabled": True,
            "gate_b_enabled": True,
            "safety_checks": {
                "policy_aligned": True,
                "capital_truth_valid": True,
                "artifacts_writing": True,
                "supabase_connected": True,
            },
        },
        runtime_root=rt,
    )
    (rt / "data/control/go_no_go_decision.json").write_text(
        json.dumps({"ready_for_first_5_trades": True}), encoding="utf-8"
    )
    (rt / "data/control/execution_mirror_results.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (rt / "data/control/operator_live_confirmation.json").write_text(
        json.dumps({"confirmed": True}), encoding="utf-8"
    )


def _strict_gate_a_proof(trade_id: str, root: Path) -> dict:
    return {
        "FINAL_EXECUTION_PROVEN": True,
        "execution_success": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
        "trade_id": trade_id,
        "runtime_root": str(root),
    }


def test_refresh_supervised_daemon_truth_chain_clears_prior_env_fingerprint_mismatch_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    a) Authority snapshot env fingerprint disagrees with current process (COINBASE_ENABLED flip).
    b) Live gate reports consistency blocker with exact mismatch token.
    c) Full ``refresh_supervised_daemon_truth_chain`` re-stamps authority from current env.
    d) Consistency + supervised live gate green; ``avenue_a_daemon_status`` shows no mismatch blocker.
    """
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")
    monkeypatch.setenv("EZRAS_SUPERVISED_CLEAN_TRADES_FOR_PROVEN", "2")
    monkeypatch.setenv("COINBASE_ENABLED", "1")
    monkeypatch.delenv("EZRAS_FIRST_20_REQUIRED_FOR_LIVE", raising=False)

    _minimal_switch_a(root)

    from trading_ai.orchestration import daemon_live_authority as dla
    from trading_ai.orchestration import runtime_runner as rr
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_supervised_runtime_allowed
    from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

    (root / "execution_proof").mkdir(parents=True)
    (root / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps(_strict_gate_a_proof("t_e2e", root)),
        encoding="utf-8",
    )
    for tid in ("e2e_a", "e2e_b"):
        append_supervised_trade_log_line(
            runtime_root=root,
            record={
                "source": "supervised_operator_session",
                "outcome_class": "clean_full_proof",
                "trade_id": tid,
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )

    monkeypatch.setenv("COINBASE_ENABLED", "0")
    snap_wrong_in = dla.compute_env_fingerprint_inputs()
    fp_wrong = dla.compute_env_fingerprint()
    monkeypatch.setenv("COINBASE_ENABLED", "1")

    ctrl = root / "data/control"
    (ctrl / "daemon_live_switch_authority.json").write_text(
        json.dumps(
            {
                "truth_version": "daemon_live_switch_authority_v1",
                "authoritative_runtime_root": str(root),
                "authoritative_env_fingerprint": fp_wrong,
                "fingerprint_inputs_canonical_snapshot": snap_wrong_in,
                "avenue_a_can_run_supervised_live_now": True,
                "avenue_a_can_run_autonomous_live_now": False,
            }
        ),
        encoding="utf-8",
    )

    ok_gate_before, bl_before = rr.live_execution_gate_ok(runtime_root=root, daemon_live_tier="supervised")
    assert ok_gate_before is False
    assert any("daemon_runtime_consistency" in str(b) for b in bl_before)
    assert any("runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority" in str(b) for b in bl_before)

    gh = {
        "truth_version": "gate_b_global_halt_truth_v1",
        "global_halt_primary_classification": "STALE_PERSISTED_STATE",
        "global_halt_is_stale": True,
        "global_halt_is_currently_authoritative": False,
        "governance_review_currently_blocking": False,
        "operator_governance_ack_present": False,
        "exact_do_not_go_live_reason_if_false": None,
        "honesty": "test",
    }
    with patch("trading_ai.reports.gate_b_global_halt_truth.write_gate_b_global_halt_truth_artifacts", lambda **_k: None):
        with patch("trading_ai.orchestration.daemon_live_authority._read_gate_b_halt", lambda _ad: gh):
            with patch(
                "trading_ai.reports.gate_b_final_go_live_truth.build_gate_b_final_go_live_truth",
                lambda **_k: {"gate_b_can_be_switched_live_now": True},
            ):
                with patch(
                    "trading_ai.orchestration.switch_live.compute_avenue_switch_live_now",
                    lambda *_a, **_k: (True, [], {}),
                ):
                    with patch(
                        "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_supervised_inputs_ok",
                        lambda **_k: (True, "ok"),
                    ):
                        with patch(
                            "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_autonomous_runtime_proven",
                            lambda **_k: (True, "ok"),
                        ):
                            with patch(
                                "trading_ai.orchestration.runtime_runner.evaluate_continuous_daemon_runtime_proven",
                                lambda **_k: True,
                            ):
                                LocalStorageAdapter(runtime_root=root).write_json(
                                    "data/control/universal_execution_loop_proof.json",
                                    {
                                        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": True,
                                        "final_execution_proven": True,
                                        "execution_lifecycle_state": "FINALIZED",
                                    },
                                )
                                LocalStorageAdapter(runtime_root=root).write_json(
                                    "data/control/operating_mode_state.json", {"mode": "halted"}
                                )
                                refresh_supervised_daemon_truth_chain(runtime_root=root)

    ok_gate_after, bl_after = rr.live_execution_gate_ok(runtime_root=root, daemon_live_tier="supervised")
    assert ok_gate_after is True
    assert bl_after == []

    sup_ok, sup_why = avenue_a_supervised_runtime_allowed(runtime_root=root)
    assert sup_ok is True, sup_why

    cons_disk = json.loads((ctrl / "daemon_runtime_consistency_truth.json").read_text(encoding="utf-8"))
    assert cons_disk.get("consistent_with_authoritative_artifacts") is True

    st = avenue_a_daemon_status(runtime_root=root)
    readiness = st.get("current_supervised_readiness_blockers") or []
    assert not any("runtime_root_or_env_fingerprint_mismatch_vs_daemon_live_switch_authority" in str(b) for b in readiness)
    assert (st.get("runtime_consistency") or {}).get("consistent_with_authoritative_artifacts") is True
    assert (st.get("supervised") or {}).get("can_run_supervised_now") is True
