"""Operator pack: combined SQL, runbooks, diagnostics, final-report structure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.deployment.operator_artifacts import write_all_operator_artifacts
from trading_ai.deployment.supabase_url_diagnostics import (
    build_supabase_runtime_diagnostics,
    hypothesis_for_schema_failure,
)


def test_combined_migration_file_exists_in_repo() -> None:
    root = Path(__file__).resolve().parents[1]
    p = root / "supabase" / "ALL_REQUIRED_LIVE_MIGRATIONS.sql"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "trade_events" in text
    assert "edge_validation_engine" in text or "edge_id" in text
    assert "trade_events_acco_columns" in text or "instrument_kind" in text


def test_hypothesis_404_vs_column() -> None:
    h404 = hypothesis_for_schema_failure(
        remote_ok=False,
        category="http_404_rest_route_or_table",
        message_excerpt="404",
    )
    assert "404" in h404 or "trade_events" in h404 or "SUPABASE_URL" in h404
    hcol = hypothesis_for_schema_failure(
        remote_ok=False,
        category="missing_column_remote_schema_drift",
        message_excerpt='column "edge_id" does not exist',
    )
    assert "column" in hcol.lower() or "migrations" in hcol.lower()


def test_supabase_url_diagnostics_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://abc123.supabase.co")
    d = build_supabase_runtime_diagnostics()
    assert d.get("looks_like_supabase_project_url") is True
    assert d.get("full_example_trade_events_url", "").endswith("/rest/v1/trade_events")


def test_operator_artifacts_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    paths = write_all_operator_artifacts()
    assert len(paths) == 3
    for _n, fp in paths.items():
        assert Path(fp).is_file()
        assert Path(fp).stat().st_size > 50


def test_schema_readiness_includes_hypothesis_and_combined_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.deployment.supabase_schema_readiness import run_supabase_schema_readiness

    out = run_supabase_schema_readiness(write_file=False)
    assert "combined_migration_file_repo" in out
    assert "ALL_REQUIRED_LIVE_MIGRATIONS.sql" in str(out.get("combined_migration_file_repo") or "")
    assert "failure_hypothesis_operator" in out
    assert "supabase_url_runtime" in out


def test_final_report_contains_manual_actions_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "deployment").mkdir(parents=True)
    frp = tmp_path / "data" / "deployment" / "final_readiness.json"
    frp.write_text(
        json.dumps(
            {
                "ready_for_first_20": False,
                "critical_blockers": ["supabase_schema:not_ready"],
                "important_blockers": [],
                "advisory_notes": [],
                "reason": "not_ready",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "deployment" / "deployment_checklist.json").write_text(
        json.dumps({"blocking_reasons": ["supabase:x"], "ready_for_live_micro_validation": False}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "deployment" / "live_validation_streak.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "deployment" / "governance_proof.json").write_text(
        json.dumps({"governance_proof_ok": True, "governance_trading_permitted": False}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "deployment" / "ops_outputs_proof.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "deployment" / "supabase_schema_readiness.json").write_text(
        json.dumps(
            {
                "schema_ready": False,
                "failure_hypothesis_operator": "LIKELY: test hypothesis",
                "required_migrations": [],
                "missing_remote_objects": [],
                "blocking_reasons": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.final_readiness_path",
        lambda: tmp_path / "data" / "deployment" / "final_readiness.json",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.checklist_json_path",
        lambda: tmp_path / "data" / "deployment" / "deployment_checklist.json",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.streak_state_path",
        lambda: tmp_path / "data" / "deployment" / "live_validation_streak.json",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.governance_proof_path",
        lambda: tmp_path / "data" / "deployment" / "governance_proof.json",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.ops_outputs_proof_path",
        lambda: tmp_path / "data" / "deployment" / "ops_outputs_proof.json",
    )
    monkeypatch.setattr(
        "trading_ai.deployment.final_readiness_report.supabase_schema_readiness_path",
        lambda: tmp_path / "data" / "deployment" / "supabase_schema_readiness.json",
    )
    from trading_ai.deployment.final_readiness_report import write_final_readiness_report

    txt = write_final_readiness_report(write_file=False)
    assert "MANUAL ACTIONS REQUIRED NOW" in txt
    assert "A. Supabase" in txt
    assert "B. Governance" in txt
    assert "C. Live execution env" in txt
