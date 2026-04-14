"""Local activation flows: operator + doctrine, seed, readiness audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.governance.operator_registry import verify_doctrine_with_registry
from trading_ai.ops import activation_control as ac
from trading_ai.ops.automation_heartbeat import DEFAULT_EXPECTED_INTERVALS, heartbeat_status


def test_activate_local_operator_registers_and_approves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = ac.activate_local_operator()
    assert out.get("ok") is True
    reg = verify_doctrine_with_registry()
    assert reg.get("ok") is True
    assert reg.get("mode") == "registry_approved"


def test_activation_seed_records_all_heartbeat_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ac.activate_local_operator()
    seed = ac.run_activation_seed()
    assert not seed.get("errors"), seed
    hb = heartbeat_status()
    comps = {c["component"]: c["status"] for c in hb["components"]}
    for comp in DEFAULT_EXPECTED_INTERVALS:
        assert comps.get(comp) != "UNKNOWN", f"missing heartbeat for {comp}: {comps}"


def test_activation_seed_and_audit_temporal_samples(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ac.activate_local_operator()
    ac.run_activation_seed()
    from trading_ai.governance.temporal_consistency import build_temporal_summary

    ts = build_temporal_summary()
    assert ts["windows"]["1d"]["sample_count"] > 0


def test_final_readiness_after_full_activation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ac.activate_local_operator()
    ac.run_activation_seed()
    ac.run_activation_flow()
    audit = ac.run_final_readiness_audit()
    assert audit.get("ok") is True
    assert audit.get("status") == "PASS"
