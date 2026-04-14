"""Tests for storage architecture and automation scope snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.config import Settings
from trading_ai.ops.automation_scope import build_automation_scope_snapshot
from trading_ai.ops.storage_architecture import (
    build_memory_storage_map,
    build_remote_readiness_plan,
    build_storage_snapshot,
)


@pytest.fixture
def runtime_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_storage_snapshot_expected_keys(runtime_tmp: Path) -> None:
    snap = build_storage_snapshot(settings=Settings())
    required = {
        "runtime_root",
        "local_state_files",
        "local_logs",
        "package_data",
        "external_services",
        "remote_persistence_enabled",
        "remote_dependencies",
        "memory_storage_mode",
        "deployment_mode",
        "encryption_at_rest",
        "governance_audit",
    }
    assert required <= set(snap.keys())
    assert snap["deployment_mode"] == "local_first"
    assert snap["runtime_root"] == str(runtime_tmp)
    assert "explicit_state" in snap["encryption_at_rest"]


def test_automation_scope_structure(runtime_tmp: Path) -> None:
    scope = build_automation_scope_snapshot()
    assert scope["runtime_root"] == str(runtime_tmp)
    assert "stops_when_macbook_off" in scope
    assert "degradation_tiers" in scope
    assert isinstance(scope["degradation_tiers"], list)
    assert "execution_dependency_graph" in scope
    assert "automation_heartbeat" in scope
    assert "heartbeat_health" in scope


def test_memory_map_and_remote_plan() -> None:
    mem = build_memory_storage_map()
    assert "primary_local_paths" in mem
    plan = build_remote_readiness_plan()
    assert plan["current_state"]
    assert "target_data_tier" in plan
