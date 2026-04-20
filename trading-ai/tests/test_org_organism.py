"""Tests for organism coordination layer (mission, experiments, readiness artifacts)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_experiment_register_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.org_organism.experiment_os import register_experiment, validate_experiment_record

    bad = validate_experiment_record({"experiment_type": "nope", "status": "draft"})
    assert bad
    good = register_experiment(
        tmp_path,
        avenue_id="A",
        gate_id="gate_b",
        parent_strategy_id="s1",
        experiment_type="replay",
        hypothesis="h",
        expected_edge_shape="bounded",
        expected_failure_mode="slippage",
        exact_success_criteria="criteria",
        exact_stop_criteria="stop",
    )
    assert good.get("ok") is True
    p = tmp_path / "data" / "control" / "organism" / "experiment_registry.json"
    assert p.is_file()


def test_mission_execution_writes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fake_controlled = {
        "rollup_answers": {"is_avenue_a_supervised_live_ready": False},
        "gate_a": {"gate_a_blockers_deduped": ["a"]},
        "gate_b": {"gate_b_blockers_deduped": ["b"]},
        "avenue_a_supervised": {"supervised_blockers_deduped": []},
    }
    fake_aut = {"active_blockers": ["x"], "can_arm_autonomous_now": False}
    with patch(
        "trading_ai.org_organism.mission_execution_layer.build_controlled_live_readiness_report",
        return_value=fake_controlled,
    ):
        with patch(
            "trading_ai.org_organism.mission_execution_layer.build_autonomous_operator_path",
            return_value=fake_aut,
        ):
            from trading_ai.org_organism.mission_execution_layer import build_mission_execution_bundle

            build_mission_execution_bundle(runtime_root=tmp_path, experiment_open_count=1)
    org = tmp_path / "data" / "control" / "organism"
    assert (org / "mission_execution_state.json").is_file()
    assert (org / "mission_progress_timeline.jsonl").is_file()


def test_opportunity_pressure_ranking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fake_controlled = {
        "rollup_answers": {"is_avenue_a_supervised_live_ready": False},
        "gate_a": {"gate_a_blockers_deduped": []},
        "gate_b": {"gate_b_blockers_deduped": []},
        "shared_infra_blockers_deduped": [],
    }
    fake_aut = {"active_blockers": ["credential_missing"]}
    with patch(
        "trading_ai.org_organism.opportunity_pressure.build_controlled_live_readiness_report",
        return_value=fake_controlled,
    ):
        with patch(
            "trading_ai.org_organism.opportunity_pressure.build_autonomous_operator_path",
            return_value=fake_aut,
        ):
            from trading_ai.org_organism.opportunity_pressure import build_opportunity_pressure_bundle

            out = build_opportunity_pressure_bundle(runtime_root=tmp_path)
    assert out["opportunity_pressure_snapshot"]["highest_priority_avenue"] == "A"


def test_autonomous_gap_delta_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fake_path = {
        "active_blockers": ["b1"],
        "can_arm_autonomous_now": False,
        "progression": {"still_missing": ["m1"]},
        "exact_next_runtime_steps": ["step"],
    }
    with patch(
        "trading_ai.org_organism.autonomous_gap_closer.build_autonomous_operator_path",
        return_value=fake_path,
    ):
        from trading_ai.org_organism.autonomous_gap_closer import build_autonomous_gap_bundle

        out = build_autonomous_gap_bundle(runtime_root=tmp_path)
    delta = out["autonomous_progress_delta"]
    assert "honesty" in delta
    p = tmp_path / "data" / "control" / "organism" / "autonomous_gap_closer.json"
    assert p.is_file()


def test_waste_detector_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fake_controlled = {
        "rollup_answers": {},
        "shared_infra_blockers_deduped": [],
        "gate_a": {"gate_a_blockers_deduped": []},
        "gate_b": {"gate_b_blockers_deduped": []},
    }
    fake_aut = {"active_blockers": ["b", "b"]}
    with patch(
        "trading_ai.org_organism.waste_detector.build_controlled_live_readiness_report",
        return_value=fake_controlled,
    ):
        with patch(
            "trading_ai.org_organism.waste_detector.build_autonomous_operator_path",
            return_value=fake_aut,
        ):
            from trading_ai.org_organism.waste_detector import build_waste_detector_bundle

            build_waste_detector_bundle(runtime_root=tmp_path)
    snap = json.loads((tmp_path / "data" / "control" / "organism" / "waste_detector_snapshot.json").read_text())
    assert snap["truth_version"] == "waste_detector_snapshot_v1"


def test_supervised_readiness_writes_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "deployment").mkdir(parents=True)
    (tmp_path / "execution_proof").mkdir(parents=True)
    with patch("trading_ai.org_organism.supervised_readiness.run_check_env") as m_env:
        m_env.return_value = {"coinbase_credentials_ok": False, "ssl_runtime": {"ssl_guard_would_pass": True}}
    with patch(
        "trading_ai.org_organism.supervised_readiness.build_controlled_live_readiness_report",
        return_value={"rollup_answers": {"is_avenue_a_supervised_live_ready": False}},
    ):
        with patch("trading_ai.org_organism.supervised_readiness.ssl_runtime_diagnostic") as m_ssl:
            m_ssl.return_value = {"ssl_guard_would_pass": True}
            from trading_ai.org_organism.supervised_readiness import build_supervised_readiness_closer

            out = build_supervised_readiness_closer(runtime_root=tmp_path)
    assert out["truth_version"] == "supervised_readiness_closer_v1"


def test_marchboard_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fake_ctrl = {
        "rollup_answers": {"is_avenue_a_supervised_live_ready": False},
        "gate_a": {"gate_a_blockers_deduped": []},
        "gate_b": {"gate_b_blockers_deduped": []},
        "shared_infra_blockers_deduped": [],
    }
    fake_aut = {"active_blockers": []}
    with patch(
        "trading_ai.org_organism.marchboard.build_controlled_live_readiness_report",
        return_value=fake_ctrl,
    ):
        with patch(
            "trading_ai.org_organism.marchboard.build_autonomous_operator_path",
            return_value=fake_aut,
        ):
            with patch(
                "trading_ai.org_organism.opportunity_pressure.build_opportunity_pressure_bundle"
            ) as m_opp:
                m_opp.return_value = {
                    "avenue_priority_queue": {"ranked": []},
                    "experiment_priority_queue": {"ranked": []},
                }
                with patch("trading_ai.org_organism.waste_detector.build_waste_detector_bundle") as m_w:
                    m_w.return_value = {"drag_sources": []}
                    from trading_ai.org_organism.marchboard import build_marchboard

                    build_marchboard(runtime_root=tmp_path, weekly=False)
    assert (tmp_path / "data" / "control" / "organism" / "daily_marchboard.json").is_file()


def test_core_system_guard_still_importable() -> None:
    from trading_ai.core.system_guard import get_system_guard

    g = get_system_guard()
    assert g is not None


