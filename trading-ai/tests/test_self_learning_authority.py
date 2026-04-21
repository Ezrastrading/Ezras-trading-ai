"""Self-learning log, authority gate, daily review, honest proposal labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.learning.authority_model import (
    block_execution_without_reasoning,
    execution_reasoning_keys,
    reasoning_payload_complete,
    weekly_proposal_envelope,
)
from trading_ai.learning.lockdown_bundle import refresh_lockdown_artifacts
from trading_ai.learning.self_learning_engine import append_learning_entry, run_self_learning_engine
from trading_ai.learning.self_learning_review import run_daily_learning_if_needed


def test_learning_entry_write(tmp_path: Path) -> None:
    p = append_learning_entry(
        {
            "event_type": "validation",
            "what_happened": "test",
            "why_it_happened": "because",
            "confidence": "high",
            "improvement_suggestion": "none",
            "requires_ceo_review": False,
        },
        runtime_root=tmp_path,
    )
    assert p.is_file()
    line = p.read_text(encoding="utf-8").strip()
    o = json.loads(line)
    assert o["event_type"] == "validation"


def test_run_self_learning_engine_appends(tmp_path: Path) -> None:
    run_self_learning_engine("failure", {"error": "x"}, runtime_root=tmp_path)
    log = tmp_path / "data" / "learning" / "system_learning_log.jsonl"
    assert log.is_file()


def test_daily_review_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    run_self_learning_engine("validation", {}, runtime_root=tmp_path)
    r1 = run_daily_learning_if_needed(runtime_root=tmp_path)
    assert r1.get("status") == "ok"
    assert (tmp_path / "data" / "review" / "daily_ai_self_learning_review.json").is_file()
    r2 = run_daily_learning_if_needed(runtime_root=tmp_path)
    assert r2.get("status") == "skipped"


def test_authority_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_AI_EXECUTION_REASONING", "true")
    blocked, _ = block_execution_without_reasoning(None)
    assert blocked is True
    ok = {k: "clear explanation with enough text" for k in execution_reasoning_keys()}
    blocked2, _ = block_execution_without_reasoning(ok)
    assert blocked2 is False


def test_reasoning_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_AI_EXECUTION_REASONING", "true")
    blocked, _ = block_execution_without_reasoning({"decision_reasoning": "ab"})
    assert blocked is True


def test_weekly_proposal_label() -> None:
    env = weekly_proposal_envelope([])
    assert env["status"] == "proposal_only_not_executed"


def test_refresh_lockdown_writes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = refresh_lockdown_artifacts(runtime_root=tmp_path)
    assert "final_audit" in out
    assert (tmp_path / "data" / "control" / "final_lockdown_audit.json").is_file()
    assert (tmp_path / "data" / "control" / "ai_performance_tracker.json").is_file()


def test_reasoning_payload_complete() -> None:
    assert reasoning_payload_complete(
        {k: "this is long enough" for k in execution_reasoning_keys()}
    )
