"""Runtime artifact refresh orchestration — fingerprints, staleness, Gate B authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.reports.runtime_artifact_refresh_manager import (
    fingerprint_dependency_set,
    run_refresh_runtime_artifacts,
)
from trading_ai.reports.runtime_artifact_registry import REGISTRY
from trading_ai.reports.gate_b_final_go_live_truth import build_gate_b_final_go_live_truth


def test_fingerprint_changes_when_file_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "x.json"
    p.write_text('{"a":1}', encoding="utf-8")
    fp1 = fingerprint_dependency_set([p])
    p.write_text('{"a":2}', encoding="utf-8")
    fp2 = fingerprint_dependency_set([p])
    assert fp1["combined_sha16"] != fp2["combined_sha16"]


def test_show_stale_only_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = run_refresh_runtime_artifacts(runtime_root=tmp_path, show_stale_only=True)
    assert out.get("mode") == "show_stale_only"
    assert not (tmp_path / "data" / "control" / "runtime_artifact_refresh_truth.json").is_file()


def test_force_refresh_writes_refresh_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "databank").mkdir(parents=True)
    (tmp_path / "data" / "databank" / "trade_events.jsonl").write_text("", encoding="utf-8")
    out = run_refresh_runtime_artifacts(runtime_root=tmp_path, force=True, include_advisory=True)
    assert (tmp_path / "data" / "control" / "runtime_artifact_refresh_truth.json").is_file()
    assert out.get("refresh_complete_and_trustworthy") is True


def test_skip_refresh_when_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "databank").mkdir(parents=True)
    (tmp_path / "data" / "databank" / "trade_events.jsonl").write_text("", encoding="utf-8")
    run_refresh_runtime_artifacts(runtime_root=tmp_path, force=True)
    out2 = run_refresh_runtime_artifacts(runtime_root=tmp_path, force=False)
    assert len(out2.get("artifacts_skipped_as_fresh") or []) >= 1


def test_final_go_live_truth_has_authority_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "gate_b_live_status.json").write_text(
        json.dumps({"gate_b_live_micro_proven": False, "gate_b_ready_for_live_orders": False}),
        encoding="utf-8",
    )
    for name in (
        "gate_b_scope_contamination_audit.json",
        "lessons_runtime_truth.json",
        "gate_b_loop_truth.json",
        "gate_b_adaptive_truth.json",
        "gate_b_operator_go_live_status.json",
        "gate_b_global_halt_truth.json",
    ):
        (ctrl / name).write_text("{}", encoding="utf-8")
    payload = build_gate_b_final_go_live_truth(runtime_root=tmp_path)
    assert "blocked_by_global_adaptive_raw" in payload
    assert "safe_activation_sequence_artifact_path_if_true" in payload


def test_registry_has_activation_bundle_last(tmp_path: Path) -> None:
    ids = [s.id for s in REGISTRY]
    assert ids.index("gate_b_final_activation_bundle") > ids.index("gate_b_final_go_live_truth")


def test_lessons_honestly_not_gate_b_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.reports.lessons_runtime_truth import build_lessons_runtime_truth

    p = build_lessons_runtime_truth(runtime_root=tmp_path)
    assert p.get("lessons_influence_candidate_ranking_gate_b") is False


def test_loop_truth_no_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.reports.gate_b_loop_truth import build_gate_b_loop_truth

    lt = build_gate_b_loop_truth(runtime_root=tmp_path)
    assert lt.get("dedicated_gate_b_scheduler_exists") is False
