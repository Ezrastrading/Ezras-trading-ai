"""Rollup snapshot structure — no raw merge without scope keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_system_rollup_has_scoped_keys_only(rt: Path) -> None:
    from trading_ai.multi_avenue.system_rollup_engine import write_system_rollup_snapshot

    p = Path(write_system_rollup_snapshot(runtime_root=rt))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["artifact"] == "system_rollup_snapshot"
    assert "rule" in data
    assert "by_avenue" in data and "by_gate" in data
    for row in data["by_gate"]:
        assert "avenue_id" in row and "gate_id" in row
