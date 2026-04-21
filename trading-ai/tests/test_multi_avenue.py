"""Multi-avenue universal layer: registries, scoped artifacts, guards, no cross-avenue contamination."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.multi_avenue.attachment import compute_auto_attach_layers, synthetic_new_avenue_attachment_demo
from trading_ai.multi_avenue.avenue_registry import build_avenue_registry_snapshot
from trading_ai.multi_avenue.contamination_guard import (
    ScopeContaminationError,
    assert_matching_scope,
    paths_must_not_share_parent,
)
from trading_ai.multi_avenue.gate_registry import build_gate_registry_snapshot
from trading_ai.multi_avenue.namespace_model import ScopeLevel, structural_model_summary
from trading_ai.multi_avenue.ratio_attachment import ratio_reserve_attachment_metadata
from trading_ai.multi_avenue.scanner_framework import build_scanner_framework_index
from trading_ai.multi_avenue.status_matrix import build_multi_avenue_status_matrix
from trading_ai.multi_avenue.universalization_audit import build_multi_avenue_universalization_audit
from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle


def test_avenue_registry_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    s = build_avenue_registry_snapshot(runtime_root=tmp_path)
    assert s.get("artifact") == "avenue_registry_snapshot"
    assert any(a.get("avenue_id") == "A" for a in s.get("avenues") or [])
    assert any(a.get("avenue_id") == "C" for a in s.get("avenues") or [])


def test_gate_registry_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    s = build_gate_registry_snapshot(runtime_root=tmp_path)
    gids = {(g.get("avenue_id"), g.get("gate_id")) for g in s.get("gates") or []}
    assert ("A", "gate_a") in gids
    assert ("B", "gate_b") in gids


def test_scoped_artifact_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = write_multi_avenue_control_bundle(runtime_root=tmp_path)
    assert "avenue_registry_snapshot_json" in out
    ctrl = tmp_path / "data" / "control"
    assert (ctrl / "multi_avenue_universalization_audit.json").is_file()
    assert (ctrl / "multi_avenue_status_matrix.json").is_file()
    assert (tmp_path / "data" / "review" / "progression_system.json").is_file()
    assert (tmp_path / "data" / "review" / "avenues" / "A" / "progression_avenue.json").is_file()
    assert (tmp_path / "data" / "control" / "avenues" / "A" / "gates" / "gate_a" / "namespace_scope_marker.json").is_file()


def test_scoped_ceo_routing_payloads() -> None:
    from trading_ai.multi_avenue.ceo_scoped import build_scoped_ceo_session_bundle
    from trading_ai.multi_avenue.namespace_model import SessionScope

    sys_b = build_scoped_ceo_session_bundle(session_scope=SessionScope.SYSTEM_WIDE.value)
    assert "legacy_artifacts" in sys_b
    av_b = build_scoped_ceo_session_bundle(session_scope=SessionScope.AVENUE.value, avenue_id="A")
    assert av_b.get("scoped_paths")


def test_scoped_progression_routing() -> None:
    from trading_ai.multi_avenue.progression_scoped import build_progression_payload

    p = build_progression_payload(scope_level=ScopeLevel.AVENUE.value, avenue_id="A")
    assert p.get("scope_level") == "avenue"
    assert p.get("avenue_id") == "A"


def test_scoped_ratio_metadata() -> None:
    m = ratio_reserve_attachment_metadata(avenue_id="A", gate_id="gate_a")
    assert m.get("classification")
    assert "mix" in (m.get("contamination_note") or "").lower()


def test_no_contamination_between_avenues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    write_multi_avenue_control_bundle(runtime_root=tmp_path)
    a_path = tmp_path / "data" / "review" / "avenues" / "A" / "progression_avenue.json"
    b_path = tmp_path / "data" / "review" / "avenues" / "B" / "progression_avenue.json"
    ja = json.loads(a_path.read_text(encoding="utf-8"))
    jb = json.loads(b_path.read_text(encoding="utf-8"))
    assert ja.get("avenue_id") == "A"
    assert jb.get("avenue_id") == "B"
    ok, _ = paths_must_not_share_parent(str(a_path), str(b_path), avenue_a="A", avenue_b="B")
    assert ok


def test_scanner_framework_readiness_unwired_gate_placeholder() -> None:
    ix = build_scanner_framework_index()
    gates = ix.get("gates") or []
    assert gates
    assert ix.get("future_avenue_behavior")


def test_multi_avenue_status_matrix_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    m = build_multi_avenue_status_matrix(runtime_root=tmp_path)
    assert m.get("artifact") == "multi_avenue_status_matrix"
    kinds = {r.get("kind") for r in m.get("rows") or []}
    assert "avenue" in kinds and "gate" in kinds


def test_synthetic_new_avenue_attachment() -> None:
    d = synthetic_new_avenue_attachment_demo("Z")
    assert "auto_attach_layers" in d
    assert d.get("execution_not_auto_attached") is True


def test_backward_compat_legacy_control_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "ratio_policy_snapshot.json").write_text('{"legacy": true}', encoding="utf-8")
    write_multi_avenue_control_bundle(runtime_root=tmp_path)
    assert json.loads((ctrl / "ratio_policy_snapshot.json").read_text(encoding="utf-8")) == {"legacy": True}


def test_universalization_audit_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    a = build_multi_avenue_universalization_audit(runtime_root=tmp_path)
    assert len(a.get("items") or []) >= 10


def test_contamination_guard_raises() -> None:
    with pytest.raises(ScopeContaminationError):
        assert_matching_scope(
            {"avenue_id": "B"},
            expected_avenue_id="A",
        )


def test_structural_model_has_layers() -> None:
    s = structural_model_summary()
    assert "layer_1_universal_intelligence" in s


def test_compute_auto_attach_layers() -> None:
    x = compute_auto_attach_layers(avenue_id="X", gate_id="gate_x")
    assert "scanner_framework_slot" in (x.get("auto_attach_layers") or [])
