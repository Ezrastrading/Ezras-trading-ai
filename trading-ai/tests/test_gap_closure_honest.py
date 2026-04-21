"""Gap-closure artifacts, honest matrix, Gate A/B classification, final-report live-truth lines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.deployment.gate_a_live_truth import gate_a_live_truth_snapshot
from trading_ai.ratios.gap_closure import (
    build_final_gap_closure_audit,
    build_honest_live_status_matrix,
    distinction_fields_reference,
    write_honest_gap_artifacts,
)
from trading_ai.ratios.recent_work_activation import build_recent_work_activation_audit
from trading_ai.ratios.trade_ratio_context import enrich_closed_trade_raw_with_ratio_context_if_absent
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def test_honest_live_status_matrix_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    m = build_honest_live_status_matrix(runtime_root=tmp_path)
    assert m.get("artifact") == "honest_live_status_matrix"
    subs = m.get("subsystems") or []
    assert len(subs) >= 20
    names = {s.get("subsystem") for s in subs}
    assert "Gate A execution" in names
    assert "Gate B execution" in names
    assert "ratio policy bundle" in names
    first = subs[0]
    assert "distinction_fields" in first
    assert "artifact_exists_at_audit_time" in first


def test_final_gap_closure_audit_items_and_distinctions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    g = build_final_gap_closure_audit(runtime_root=tmp_path)
    assert g.get("distinction_fields_reference") == distinction_fields_reference()
    ids = {it["id"] for it in (g.get("items") or [])}
    for letter in "ABCDEFGHIJKLMNOPQRS":
        assert any(x.startswith(letter + "_") for x in ids)
    for it in g.get("items") or []:
        assert "distinction_fields" in it
        assert it["distinction_fields"]["code_exists"] == bool(it.get("exists_in_repo"))


def test_write_honest_gap_artifacts_writes_control_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = write_honest_gap_artifacts(runtime_root=tmp_path)
    ctrl = tmp_path / "data" / "control"
    assert (ctrl / "honest_live_status_matrix.json").is_file()
    assert (ctrl / "final_gap_closure_audit.json").is_file()
    assert "honest_live_status_matrix_json" in out


def test_advisory_vs_enforced_labels_in_gap_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    g = build_final_gap_closure_audit(runtime_root=tmp_path)
    by_id = {it["id"]: it for it in (g.get("items") or [])}
    assert by_id["A_ratio_framework_enforcement"]["current_truth_status"] == "runtime_readable_not_order_enforced"
    assert by_id["C_ratio_context_trade_paths"]["enforced_vs_informational"] == "advisory_to_runtime"


def test_gate_a_truth_classification_advisory() -> None:
    snap = gate_a_live_truth_snapshot()
    assert snap.get("gate") == "A"
    cls = snap.get("classification") or {}
    assert cls.get("ratio_framework_role") == "runtime_readable_not_order_enforced"


def test_gate_b_truth_states_and_ratio_advisory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    r = gate_b_live_status_report()
    assert r.get("gate_b_production_state") == "STATE_A_intentionally_disabled"
    adv = r.get("ratio_reserve_advisory") or {}
    assert adv.get("honest_classification") == "advisory_runtime_context_not_order_enforced"
    assert adv.get("ratio_aware") is True


def test_gate_ratio_bundle_read_does_not_write_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.execution.routing.integration.gate_hooks import gate_ratio_and_reserve_bundle

    gate_ratio_and_reserve_bundle(write_ratio_artifacts=False)
    assert not (tmp_path / "data" / "control" / "ratio_policy_snapshot.json").is_file()


def test_enrich_closed_trade_ratio_context_safe() -> None:
    raw = {
        "trade_id": "t1",
        "avenue_name": "coinbase",
        "trading_gate": "gate_a",
        "strategy_id": "s",
        "market_snapshot_json": {"existing": True},
    }
    out = enrich_closed_trade_raw_with_ratio_context_if_absent(raw)
    assert out.get("ratio_context", {}).get("ratio_policy_version")
    assert out.get("market_snapshot_json") == {"existing": True}


def test_daily_ratio_review_llm_honest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.ratios.daily_ratio_review import build_daily_ratio_review_payload

    p = build_daily_ratio_review_payload(runtime_root=tmp_path)
    assert p.get("llm_orchestration_status") == "not_yet_wired"
    assert "next_integration_step_for_llm_review" in p


def test_final_readiness_report_live_truth_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    dd = tmp_path / "data" / "deployment"
    dd.mkdir(parents=True)
    minimal = {
        "ready_for_first_20": False,
        "critical_blockers": [],
        "important_blockers": [],
        "advisory_notes": [],
        "live_truth_plain_english": {
            "what_is_code_ready_but_not_yet_live": ["x"],
            "what_is_runtime_proven": ["y"],
            "what_is_advisory_only": ["z"],
            "what_needs_external_deploy_or_scheduler": ["cron"],
            "what_first_20_depends_on_that_is_still_unproven": [],
            "gate_a_live_truth_summary": {"k": "v"},
            "gate_b_live_truth_summary": {"a": 1},
        },
    }
    (dd / "final_readiness.json").write_text(json.dumps(minimal), encoding="utf-8")
    for name in (
        "deployment_checklist.json",
        "live_validation_streak.json",
        "governance_proof.json",
        "ops_outputs_proof.json",
        "supabase_schema_readiness.json",
    ):
        (dd / name).write_text("{}", encoding="utf-8")

    from trading_ai.deployment.final_readiness_report import write_final_readiness_report

    text = write_final_readiness_report(write_file=False)
    assert "LIVE TRUTH (honest — from readiness JSON)" in text
    assert "What is code-ready but not yet live:" in text
    assert "  - x" in text


def test_recent_work_activation_distinction_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    aud = build_recent_work_activation_audit(runtime_root=tmp_path)
    assert "distinction_fields_reference" in aud
    for row in aud.get("items") or []:
        assert "distinction_fields" in row
        assert "next_status_if_external" in row
