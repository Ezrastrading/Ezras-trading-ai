"""Final wiring: reconciliation probe context, databank verify, tagging, Gate B status."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_ai.shark.execution_live import submit_order
from trading_ai.shark.models import ExecutionIntent, HuntType

from trading_ai.deployment.databank_artifact_verify import verify_local_databank_artifacts
from trading_ai.deployment.readiness_decision import _latest_micro_validation_run_json, _reconciliation_probe_context
from trading_ai.nte.databank.trade_tagging_contract import validate_evolution_tags
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def test_reconciliation_probe_includes_inventory_delta_baselines(tmp_path: Path) -> None:
    runs = tmp_path / "live_validation_runs"
    runs.mkdir(parents=True)
    payload = {
        "spot_snapshot_before": {
            "exchange_base_qty": 0.00013,
            "internal_base_qty": 0.0,
        }
    }
    (runs / "live_validation_001.json").write_text(json.dumps(payload), encoding="utf-8")
    with patch(
        "trading_ai.deployment.readiness_decision.live_validation_runs_dir",
        return_value=runs,
    ):
        ctx = _reconciliation_probe_context("BTC-USD")
    assert ctx.get("reconciliation_mode") == "inventory_delta"
    assert "baseline_exchange_base_qty" in ctx


def test_databank_artifact_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    root = tmp_path / "databank"
    root.mkdir(parents=True)
    (root / "trade_events.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (root / "daily_trade_summary.json").write_text('{"rollups":[]}', encoding="utf-8")
    (root / "weekly_trade_summary.json").write_text('{"rollups":[]}', encoding="utf-8")
    v = verify_local_databank_artifacts(trade_id=None)
    assert v.get("all_core_ok") is True


def test_evolution_tags_gate_a_row_ok() -> None:
    row = {
        "trade_id": "t1",
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "strategy_id": "x",
        "asset": "BTC-USD",
        "trading_gate": "gate_a",
    }
    assert validate_evolution_tags(row) == []


def test_gate_b_state_a_explicit() -> None:
    r = gate_b_live_status_report()
    assert r.get("gate_b_live_execution_enabled") is False
    assert r.get("gate_b_production_state") == "STATE_A_intentionally_disabled"
    assert isinstance(r.get("coinbase_single_leg_runtime_policy"), dict)
    assert isinstance(r.get("gate_b_lifecycle"), dict)
    assert "readiness_first_20_is_gate_a_scope" in (r.get("gate_b_lifecycle") or {})


def test_gate_b_state_b_when_enabled_without_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GATE_B_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    r = gate_b_live_status_report()
    assert r.get("gate_b_production_state") == "STATE_B_live_enabled_not_validated"
    assert r.get("gate_b_validation_status") == "pending_validation"
    assert r.get("gate_b_ready_for_live") is False


def test_gate_b_state_c_when_validation_file_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GATE_B_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "gate_b_validation.json").write_text(
        json.dumps(
            {
                "validated_at": "2026-01-01T00:00:00+00:00",
                "proof_source": "test",
                "micro_validation_pass": True,
                "failed_validation": False,
            }
        ),
        encoding="utf-8",
    )
    r = gate_b_live_status_report()
    assert r.get("gate_b_production_state") == "STATE_C_live_validated"
    assert r.get("gate_b_validation_status") == "validated"
    assert r.get("gate_b_ready_for_live") is False
    assert r.get("gate_b_staged_micro_proven") is True
    assert r.get("gate_b_live_micro_proven") is False


def test_gate_b_ready_for_live_requires_live_venue_proof(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GATE_B_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "gate_b_validation.json").write_text(
        json.dumps(
            {
                "validated_at": "2026-01-01T00:00:00+00:00",
                "proof_source": "test",
                "micro_validation_pass": True,
                "failed_validation": False,
                "live_venue_micro_validation_pass": True,
            }
        ),
        encoding="utf-8",
    )
    r = gate_b_live_status_report()
    assert r.get("gate_b_ready_for_live") is True
    assert r.get("gate_b_live_micro_proven") is True
    assert r.get("readiness_state") == "live_ready"


def test_latest_micro_validation_empty(tmp_path: Path) -> None:
    with patch("trading_ai.deployment.readiness_decision.live_validation_runs_dir", return_value=Path("/nonexistent_run_dir_xxx")):
        assert _latest_micro_validation_run_json() == {}


def test_kalshi_gate_b_adaptive_blocks_when_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_B_LIVE_EXECUTION_ENABLED", "true")
    intent = ExecutionIntent(
        market_id="KXTEST-24",
        outlet="kalshi",
        side="yes",
        stake_fraction_of_capital=0.01,
        edge_after_fees=0.02,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.KALSHI_CONVERGENCE],
        source="test",
        shares=5,
    )
    with patch(
        "trading_ai.control.system_execution_lock.require_live_execution_allowed",
        return_value=(True, "ok"),
    ):
        with patch(
            "trading_ai.control.live_adaptive_integration.run_live_adaptive_evaluation",
            return_value={
                "allow_new_trades": False,
                "mode": "defensive",
                "size_multiplier_effective": 0.5,
            },
        ):
            with patch(
                "trading_ai.control.live_adaptive_integration.build_live_operating_snapshot",
                return_value=MagicMock(),
            ):
                res = submit_order(intent)
    assert res.success is False
    assert res.status == "adaptive_os_blocked"
