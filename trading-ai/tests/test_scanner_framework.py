"""Scanner framework status JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_scanner_framework_status_has_gate_flags(rt: Path) -> None:
    from trading_ai.multi_avenue.scanner_framework import write_scanner_framework_status

    p = Path(write_scanner_framework_status(runtime_root=rt))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["artifact"] == "scanner_framework_status"
    assert data["gates"]
