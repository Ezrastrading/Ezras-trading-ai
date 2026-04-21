"""Scope guards and contamination assertions."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.multi_avenue.contamination_guard import (
    ScopeContaminationError,
    contamination_assert_paths_distinct_across_avenues,
)
from trading_ai.multi_avenue.scope_guards import ScopeViolationError, validate_trade_scope


def test_paths_must_not_collide_across_avenues() -> None:
    contamination_assert_paths_distinct_across_avenues(
        "/x/a.json",
        "/y/b.json",
        avenue_id_a="A",
        avenue_id_b="B",
    )
    with pytest.raises(ScopeContaminationError):
        contamination_assert_paths_distinct_across_avenues(
            "/same/p.json",
            "/same/p.json",
            avenue_id_a="A",
            avenue_id_b="B",
        )


def test_validate_trade_scope_mismatch_logs_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = tmp_path
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    with pytest.raises(ScopeViolationError):
        validate_trade_scope(
            {"avenue_id": "B", "gate_id": "gate_b"},
            expected_avenue_id="A",
            expected_gate_id="gate_a",
            runtime_root=rt,
        )
