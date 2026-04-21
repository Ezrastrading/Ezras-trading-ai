"""Runtime runner lock, live gate, and daemon proof scaffolding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.orchestration import runtime_runner as rr


def test_exclusive_lock_second_instance_fails(tmp_path: Path) -> None:
    assert rr.try_acquire_lock(runtime_root=tmp_path, pid=123) is True
    assert rr.try_acquire_lock(runtime_root=tmp_path, pid=456) is False
    rr.release_lock(runtime_root=tmp_path)
    assert rr.try_acquire_lock(runtime_root=tmp_path, pid=789) is True
    rr.release_lock(runtime_root=tmp_path)


def test_live_execution_requires_operator_and_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNNER_MODE", "live_execution")
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("EZRAS_OPERATOR_LIVE_CONFIRMED", raising=False)
    ok, blockers = rr.live_execution_gate_ok(runtime_root=tmp_path)
    assert ok is False
    assert any("operator" in b for b in blockers)


def test_daemon_verification_enables_proven_flag(tmp_path: Path) -> None:
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    p = tmp_path / "data" / "control" / "runtime_runner_daemon_verification.json"
    p.write_text(
        json.dumps({"lock_exclusivity_verified": True, "failure_stop_verified": True}),
        encoding="utf-8",
    )
    assert rr.evaluate_continuous_daemon_runtime_proven(runtime_root=tmp_path) is True


def test_run_cycle_respects_daemon_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNNER_MODE", "tick_only")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True, exist_ok=True)
    ks = tmp_path / "data" / "control" / "system_kill_switch.json"
    ks.write_text(json.dumps({"active": True}), encoding="utf-8")
    out = rr.run_cycle(runtime_root=tmp_path, cycle_index=1)
    assert out.get("ok") is False
    assert out.get("daemon_aborted")
