"""Gap 2 — organism stress harness produces reports and stays parse-clean."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.runtime_proof.organism_stress_harness import (
    run_organism_soak_harness,
    run_organism_stress_harness,
)


def test_stress_harness_writes_reports_and_clean_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    out = run_organism_stress_harness(tmp_path, iterations=22, review_cycle_every=11)
    sp = Path(out["output_dir"])
    for name in (
        "runtime_proof_report.json",
        "scheduler_stress_report.json",
        "federation_stress_report.json",
        "artifact_integrity_report.json",
    ):
        p = sp / name
        assert p.is_file(), name
        json.loads(p.read_text(encoding="utf-8"))
    assert out["scheduler_stress_report"]["malformed_jsonl_lines"] == 0
    assert out["summary"]["governance_all_ok"] is True


def test_soak_harness_writes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    out = run_organism_soak_harness(tmp_path, test_mode=True, min_iterations=24, review_cycle_every=8)
    sp = Path(out["output_dir"])
    for name in (
        "soak_runtime_report.json",
        "soak_scheduler_report.json",
        "soak_artifact_integrity_report.json",
        "soak_report_summary.json",
    ):
        p = sp / name
        assert p.is_file(), name
        json.loads(p.read_text(encoding="utf-8"))
    assert out["soak_report_summary"]["malformed_jsonl_lines"] == 0
