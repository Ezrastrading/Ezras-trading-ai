"""Universal avenue/gate agnostic truth artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.daemon_testing.daemon_artifact_writers import write_universal_avenue_gate_agnostic_truth


def test_agnostic_truth_has_execution_and_shark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = write_universal_avenue_gate_agnostic_truth(runtime_root=tmp_path, matrix_summary={})
    assert out.get("execution_routes")
    assert "kalshi" in str(out.get("shark_business_avenues"))
