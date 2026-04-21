"""Failure injection truth artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_failure_injection_truth


def test_failure_injection_truth_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = write_daemon_failure_injection_truth(runtime_root=tmp_path, matrix_rows=None)
    assert out.get("truth_version")
    p = tmp_path / "data" / "control" / "daemon_failure_injection_truth.json"
    assert p.is_file()
    assert "fi_kill_switch_trip" in (out.get("failures") or {})
