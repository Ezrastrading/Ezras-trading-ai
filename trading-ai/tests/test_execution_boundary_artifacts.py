"""Gap 4 — boundary proof JSON artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.runtime_proof.execution_boundary_report import write_boundary_artifacts


def test_boundary_artifacts_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    paths = write_boundary_artifacts(tmp_path)
    for p in paths.values():
        assert p.is_file()
        json.loads(p.read_text(encoding="utf-8"))
