"""Autonomous live readiness authority — honest defaults without closure bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.daemon_testing.daemon_artifact_writers import write_autonomous_live_readiness_authority


def test_autonomous_readiness_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    (ctrl / "daemon_failure_injection_truth.json").write_text(
        json.dumps({"truth_version": "daemon_failure_injection_truth_v1", "failures": {}}),
        encoding="utf-8",
    )
    (ctrl / "daemon_rebuy_certification.json").write_text(
        json.dumps({"rebuy_contract_proven_fake": True, "rebuy_contract_runtime_proven": False}),
        encoding="utf-8",
    )
    out = write_autonomous_live_readiness_authority(runtime_root=tmp_path)
    assert out.get("truth_version")
    routes = out.get("per_avenue_gate") or []
    assert any(r.get("avenue_id") == "A" for r in routes)
