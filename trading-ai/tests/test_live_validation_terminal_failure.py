"""Terminal failure taxonomy and daemon failure envelopes — no silent failures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.runtime_proof.live_validation_terminal_failure import (
    FAILURE_CODE_BUY_BLOCKED_DUPLICATE_GUARD,
    FAILURE_CODE_BUY_BLOCKED_GOVERNANCE,
    FAILURE_CODE_BUY_ORDER_SUBMIT_FAILED,
    FAILURE_CODE_SELL_FILL_NOT_CONFIRMED,
    FAILURE_CODE_SUPABASE_SYNC_FAILED,
    attach_terminal_failure_fields,
    classify_early_guard_failure,
    proof_contract_violation_messages,
)


def test_duplicate_guard_maps_to_taxonomy() -> None:
    err = "failsafe_blocked:duplicate_trade_guard:duplicate_trade_window"
    code, stage, reason = classify_early_guard_failure(err)
    assert code == FAILURE_CODE_BUY_BLOCKED_DUPLICATE_GUARD
    assert stage == "pre_buy"
    assert "duplicate" in reason.lower()


def test_buy_failed_maps_to_order_submit() -> None:
    code, stage, _ = classify_early_guard_failure("buy_failed:insufficient_balance")
    assert code == FAILURE_CODE_BUY_ORDER_SUBMIT_FAILED
    assert stage == "buy"


def test_attach_pipeline_supabase_false() -> None:
    base = {
        "execution_success": True,
        "FINAL_EXECUTION_PROVEN": False,
        "error": None,
        "supabase_synced": False,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_sync_diagnostics": {"last_error": "timeout"},
    }
    attach_terminal_failure_fields(base)
    assert base["failure_code"] == FAILURE_CODE_SUPABASE_SYNC_FAILED
    assert base["failure_reason"]
    assert base["error"] == base["failure_reason"]


def test_attach_sell_fill_not_confirmed() -> None:
    base = {
        "execution_success": False,
        "FINAL_EXECUTION_PROVEN": False,
        "error": None,
        "buy_fill_confirmed": True,
        "sell_fill_confirmed": False,
        "sell_leg_diagnostics": {
            "order_id_sell": "s1",
            "place_success": True,
            "actual_missing_or_false_input": "no_sell_fill_truth",
        },
        "buy_leg_diagnostics": {},
        "local_write_diagnostics": {"databank_process_ok": True},
        "pipeline": {
            "trade_memory_updated": True,
            "trade_events_appended": True,
            "federated_includes_trade_id": True,
        },
        "partial_failure_codes": [],
    }
    attach_terminal_failure_fields(base)
    assert base["failure_code"] == FAILURE_CODE_SELL_FILL_NOT_CONFIRMED
    assert base["error"]


def test_proof_contract_violations_lists_false_booleans() -> None:
    g = {
        "FINAL_EXECUTION_PROVEN": False,
        "execution_success": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_synced": False,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
    }
    v = proof_contract_violation_messages(g)
    assert any("supabase" in x for x in v)


def test_final_proven_false_explicit_conditions() -> None:
    g = {
        "FINAL_EXECUTION_PROVEN": False,
        "execution_success": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
    }
    v = proof_contract_violation_messages(g)
    assert any("FINAL_EXECUTION_PROVEN" in x for x in v)


def test_failed_daemon_cycle_writes_non_null_failure_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")

    proof = {
        "execution_success": False,
        "FINAL_EXECUTION_PROVEN": False,
        "error": "governance_blocked:test",
        "failure_stage": "pre_buy",
        "failure_code": FAILURE_CODE_BUY_BLOCKED_GOVERNANCE,
        "failure_reason": "governance_blocked:test",
        "final_execution_proven": False,
        "trade_id": "live_exec_x",
        "product_id": "BTC-USD",
        "order_id_buy": None,
        "order_id_sell": None,
    }

    with patch("trading_ai.orchestration.runtime_runner.daemon_abort_conditions", return_value=(False, "", False)):
        with patch(
            "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_supervised_runtime_allowed",
            return_value=(True, "ok"),
        ):
            with patch(
                "trading_ai.orchestration.avenue_a_live_daemon._rebuy_allows_next_entry",
                return_value=(True, ""),
            ):
                with patch(
                    "trading_ai.safety.failsafe_guard.peek_duplicate_trade_window_would_block_entry",
                    return_value=False,
                ):
                    with patch(
                        "trading_ai.runtime_proof.live_execution_validation.run_single_live_execution_validation",
                        return_value=proof,
                    ):
                        with patch(
                            "trading_ai.universal_execution.gate_b_proof_bridge.try_emit_universal_loop_proof_from_gate_a_file",
                            return_value={"emitted": False, "reason": "no_live_execution_validation_json"},
                        ):
                            from trading_ai.orchestration.avenue_a_live_daemon import run_avenue_a_daemon_once

                            out = run_avenue_a_daemon_once(runtime_root=tmp_path, product_id="BTC-USD")

    assert out["ok"] is False
    assert out.get("failure_reason")
    assert out.get("failure_code")

    fail_path = tmp_path / "data/control/runtime_runner_last_failure.json"
    body = json.loads(fail_path.read_text(encoding="utf-8"))
    assert body.get("failure_reason")
    assert body["avenue_a_daemon"]["live_validation"].get("failure_code")


def test_persist_successful_gate_a_proof_overwrites_stale_file(tmp_path: Path) -> None:
    from trading_ai.runtime_proof.live_execution_validation import persist_successful_gate_a_proof_to_disk

    ep = tmp_path / "execution_proof"
    ep.mkdir(parents=True)
    stale = {
        "FINAL_EXECUTION_PROVEN": False,
        "execution_success": False,
        "error": "missing_or_invalid_LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_or_autonomous_ack (daemon_active=True)",
        "failure_code": "proof_contract_not_satisfied",
    }
    (ep / "live_execution_validation.json").write_text(json.dumps(stale), encoding="utf-8")

    validation_out = {
        "execution_success": True,
        "FINAL_EXECUTION_PROVEN": True,
        "execution_profile": "gate_a",
        "runtime_root": str(tmp_path),
        "trade_id": "live_exec_fresh",
        "product_id": "BTC-USD",
        "order_id_buy": "buy-1",
        "order_id_sell": "sell-1",
        "proof": {
            "execution_success": True,
            "coinbase_order_verified": True,
            "databank_written": True,
            "supabase_synced": True,
            "governance_logged": True,
            "packet_updated": True,
            "scheduler_stable": True,
            "FINAL_EXECUTION_PROVEN": True,
            "partial_failure_codes": [],
            "pnl_calculation_verified": True,
            "trade_id": "live_exec_fresh",
            "product_id": "BTC-USD",
        },
    }
    persist_successful_gate_a_proof_to_disk(tmp_path, validation_out)
    disk = json.loads((ep / "live_execution_validation.json").read_text(encoding="utf-8"))
    assert disk.get("FINAL_EXECUTION_PROVEN") is True
    assert disk.get("execution_success") is True
    assert disk.get("error") is None
    assert disk.get("failure_code") is None
    assert disk.get("failure_reason") is None
    assert Path(disk["runtime_root"]).resolve() == tmp_path.resolve()
    assert disk.get("trade_id") == "live_exec_fresh"


def test_successful_cycle_not_marked_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")
    ctrl = tmp_path / "data/control"
    ctrl.mkdir(parents=True)
    (ctrl / "universal_execution_loop_proof.json").write_text(
        json.dumps({"last_trade_id": "live_exec_ok"}),
        encoding="utf-8",
    )

    proof = {
        "execution_success": True,
        "FINAL_EXECUTION_PROVEN": True,
        "execution_profile": "gate_a",
        "runtime_root": str(tmp_path),
        "error": None,
        "failure_stage": None,
        "failure_code": None,
        "failure_reason": None,
        "final_execution_proven": True,
        "trade_id": "live_exec_ok",
        "product_id": "BTC-USD",
        "order_id_buy": "b1",
        "order_id_sell": "s1",
        "proof": {
            "execution_success": True,
            "coinbase_order_verified": True,
            "databank_written": True,
            "supabase_synced": True,
            "governance_logged": True,
            "packet_updated": True,
            "scheduler_stable": True,
            "FINAL_EXECUTION_PROVEN": True,
            "trade_id": "live_exec_ok",
            "product_id": "BTC-USD",
            "runtime_root": str(tmp_path),
            "error": None,
            "failure_stage": None,
            "failure_code": None,
            "failure_reason": None,
            "final_execution_proven": True,
            "partial_failure_codes": [],
            "pnl_calculation_verified": True,
        },
    }

    with patch("trading_ai.orchestration.runtime_runner.daemon_abort_conditions", return_value=(False, "", False)):
        with patch(
            "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_supervised_runtime_allowed",
            return_value=(True, "ok"),
        ):
            with patch(
                "trading_ai.orchestration.avenue_a_live_daemon._rebuy_allows_next_entry",
                return_value=(True, ""),
            ):
                with patch(
                    "trading_ai.safety.failsafe_guard.peek_duplicate_trade_window_would_block_entry",
                    return_value=False,
                ):
                    with patch(
                        "trading_ai.runtime_proof.live_execution_validation.run_single_live_execution_validation",
                        return_value=proof,
                    ):
                        with patch(
                            "trading_ai.universal_execution.gate_b_proof_bridge.try_emit_universal_loop_proof_from_gate_a_file",
                            return_value={"emitted": True, "reason": "mapped"},
                        ):
                            from trading_ai.orchestration.avenue_a_live_daemon import run_avenue_a_daemon_once

                            out = run_avenue_a_daemon_once(runtime_root=tmp_path, product_id="BTC-USD")

    assert out["ok"] is True
    assert (tmp_path / "data/control/runtime_runner_last_success.json").is_file()
