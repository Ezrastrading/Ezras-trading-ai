"""Daemon-level rebuy certification artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_rebuy_certification


def test_rebuy_certification_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    out = write_daemon_rebuy_certification(runtime_root=tmp_path, matrix_rows=None)
    assert "rebuy_contract_proven_fake" in out
    assert out.get("rebuy_contract_runtime_proven") is False
