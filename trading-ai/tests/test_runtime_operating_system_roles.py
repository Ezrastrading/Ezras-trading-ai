from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_ops_and_research_ticks_run_and_live_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Explicitly try to trick it into live; it should block.
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.runtime.operating_system import tick_ops_once, tick_research_once

    ops = tick_ops_once(runtime_root=tmp_path)
    assert ops.get("ok") is True

    res = tick_research_once(runtime_root=tmp_path, skip_models=True)
    assert res.get("ok") is True

    # Ops should write an outcome snapshot under runtime_root.
    p = tmp_path / "data" / "control" / "ops_outcome_ingestion_snapshot.json"
    assert p.is_file()
    blob = json.loads(p.read_text(encoding="utf-8"))
    assert blob.get("truth_version") == "ops_outcome_ingestion_snapshot_v1"


def test_role_lock_prevents_two_ops_daemons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import try_acquire_role_lock

    ok1, _, _ = try_acquire_role_lock(role="ops", holder_id="h1", runtime_root=tmp_path, ttl_seconds=30)
    ok2, why2, _ = try_acquire_role_lock(role="ops", holder_id="h2", runtime_root=tmp_path, ttl_seconds=30)
    assert ok1 is True
    assert ok2 is False
    assert "role_lock_held:ops" in why2

