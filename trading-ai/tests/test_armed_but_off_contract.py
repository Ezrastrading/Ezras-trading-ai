"""Dual-gate autonomous live enable — no default live orders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.orchestration.autonomous_daemon_live_contract import (
    autonomous_daemon_may_submit_live_orders,
    arm_autonomous_daemon_live_enable_file,
)


def test_live_orders_require_artifact_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED", raising=False)
    ok, bl = autonomous_daemon_may_submit_live_orders(runtime_root=tmp_path)
    assert ok is False
    assert any("artifact" in x or "confirmed" in x for x in bl)

    (tmp_path / "data/control").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/control/autonomous_daemon_live_enable.json").write_text(
        json.dumps({"schema_version": 1, "confirmed": True}),
        encoding="utf-8",
    )
    ok2, bl2 = autonomous_daemon_may_submit_live_orders(runtime_root=tmp_path)
    assert ok2 is False
    assert any("EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED" in x for x in bl2)

    monkeypatch.setenv("EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED", "true")
    ok3, bl3 = autonomous_daemon_may_submit_live_orders(runtime_root=tmp_path)
    assert ok3 is True
    assert bl3 == []


def test_arm_cli_writes_structure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = arm_autonomous_daemon_live_enable_file(
        runtime_root=tmp_path,
        confirmed=False,
        avenue_ids=["A"],
        gate_ids=["gate_a"],
        operator="test",
        note="pytest",
    )
    assert out.get("confirmed") is False
    assert (tmp_path / "data/control/autonomous_daemon_live_enable.json").is_file()
