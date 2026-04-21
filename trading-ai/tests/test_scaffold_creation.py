"""Scaffold idempotence and path layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_ensure_avenue_scaffold_creates_namespace_and_control(rt: Path) -> None:
    from trading_ai.multi_avenue.auto_scaffold import ensure_avenue_scaffold

    r = ensure_avenue_scaffold("Z", runtime_root=rt)
    assert r["avenue_id"] == "Z"
    assert (rt / "data/review/avenues/Z/namespace/scope_manifest.json").is_file()
    assert (rt / "data/control/avenues/Z/avenue_status_snapshot.json").is_file()


def test_ensure_gate_scaffold_idempotent(rt: Path) -> None:
    from trading_ai.multi_avenue.auto_scaffold import ensure_gate_scaffold

    a = ensure_gate_scaffold("Z", "gate_z", runtime_root=rt)
    b = ensure_gate_scaffold("Z", "gate_z", runtime_root=rt)
    assert (rt / "data/review/avenues/Z/gates/gate_z/scanner_metadata.json").is_file()
    assert a["created_paths"] and not b["created_paths"]
