"""Executable proofs for supervised now-live autonomy stack (no venue live orders)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def _run_mod(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["EZRAS_RUNTIME_ROOT"] = str(tmp_path)
    env.setdefault("NTE_EXECUTION_MODE", "paper")
    env.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    env.setdefault("COINBASE_EXECUTION_ENABLED", "false")
    return subprocess.run(
        [sys.executable, "-m", "trading_ai.runtime", *argv],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_supervisor_writes_loop_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    out = run_role_supervisor_once(role="ops", runtime_root=tmp_path, skip_models=True, force_all_due=True)
    assert out.get("ok") is True
    st = tmp_path / "data" / "control" / "operating_system" / "loop_status_ops.json"
    assert st.is_file()
    loops = json.loads(st.read_text(encoding="utf-8")).get("loops") or {}
    assert "scanner_cycle" in loops


def test_scanner_snapshot_increments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.multi_avenue.lifecycle_hooks import on_scanner_cycle_export

    a = on_scanner_cycle_export(runtime_root=tmp_path)
    b = on_scanner_cycle_export(runtime_root=tmp_path)
    assert int(b["scan_seq"]) == int(a["scan_seq"]) + 1


def test_simulated_fill_chain_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.simulated_fill_lifecycle import advance_simulated_fill_once

    for _ in range(12):
        advance_simulated_fill_once(runtime_root=tmp_path)
    summ = tmp_path / "data" / "control" / "simulated_fill_chain" / "reconciliation_summary.json"
    assert summ.is_file()


def test_role_lock_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import release_role_lock, try_acquire_role_lock

    ok1, _, _ = try_acquire_role_lock(role="ops", holder_id="a", runtime_root=tmp_path, ttl_seconds=90.0)
    ok2, why, _ = try_acquire_role_lock(role="ops", holder_id="b", runtime_root=tmp_path, ttl_seconds=90.0)
    assert ok1 is True
    assert ok2 is False
    assert "role_lock_held" in why
    release_role_lock(role="ops", holder_id="a", runtime_root=tmp_path)


def test_live_guard_cli(tmp_path: Path) -> None:
    cp = _run_mod(tmp_path, "live-guard-proof")
    assert cp.returncode == 0, cp.stderr
    proof = tmp_path / "data" / "control" / "authoritative_live_guard_proof.json"
    assert proof.is_file()


def test_accelerated_sim_cli(tmp_path: Path) -> None:
    cp = _run_mod(tmp_path, "accelerated-sim", "--cycles", "8", "--skip-models")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    v = tmp_path / "data" / "control" / "accelerated_autonomy_sim_verdict.json"
    data = json.loads(v.read_text(encoding="utf-8"))
    assert data.get("ok") is True


def test_deploy_systemd_contract_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.service_contract_verify import verify_systemd_unit_contract_templates

    ok, out = verify_systemd_unit_contract_templates(repo_root=ROOT)
    assert ok is True, out


def test_task_routing_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    reg = tmp_path / "bots.json"
    reg.write_text(
        json.dumps(
            {
                "truth_version": "bot_registry_v2",
                "bots": [
                    {
                        "bot_id": "b1",
                        "avenue": "A",
                        "gate": "gate_a",
                        "role": "learning",
                        "lifecycle_state": "active",
                    },
                    {
                        "bot_id": "b2",
                        "avenue": "B",
                        "gate": "gate_b",
                        "role": "risk",
                        "lifecycle_state": "active",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(reg))
    from trading_ai.global_layer.task_router import route_task_shadow

    t1 = route_task_shadow(
        avenue="A",
        gate="gate_a",
        task_type="t1",
        source_bot_id="x",
        role="learning",
        evidence_ref="e1",
    )
    t2 = route_task_shadow(
        avenue="B",
        gate="gate_b",
        task_type="t2",
        source_bot_id="x",
        role="risk",
        evidence_ref="e2",
    )
    assert t1.get("avenue") == "A" and t1.get("gate") == "gate_a"
    assert t2.get("avenue") == "B" and t2.get("gate") == "gate_b"


def test_restart_safe_task_intake_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.global_layer.task_intake import run_task_intake_once
    from trading_ai.global_layer.task_router import route_task_shadow

    route_task_shadow(
        avenue="A",
        gate="none",
        task_type="restart_smoke",
        source_bot_id="s",
        role="learning",
        evidence_ref="ev",
    )
    a = run_task_intake_once(runtime_root=tmp_path)
    b = run_task_intake_once(runtime_root=tmp_path)
    assert a.get("ok") is not False
    assert b.get("ok") is not False
