"""Daemon matrix runner — fake/replay smoke; avenue isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.daemon_testing.daemon_assertions import assert_autonomous_never_proven_from_fake
from trading_ai.daemon_testing.daemon_matrix_runner import run_daemon_verification_matrix
from trading_ai.daemon_testing.registry import iter_avenue_gate_pairs


def test_fake_matrix_non_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = run_daemon_verification_matrix(runtime_root=tmp_path, levels=("fake",))
    assert out["row_count"] > 100
    assert out["summary"]["failed_count"] >= 0


def test_replay_uses_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    fx = Path(__file__).resolve().parent / "fixtures" / "daemon_replay" / "minimal_loop_proof.json"
    out = run_daemon_verification_matrix(runtime_root=tmp_path, levels=("replay",))
    assert out["row_count"] > 0
    first = (out["rows"] or [{}])[0]
    assert first.get("adapter_truth_class") == "simulated_real_artifact_replay"
    assert fx.is_file()


def test_autonomous_never_proven_on_fake_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.daemon_testing.daemon_fake_adapters import build_fake_row
    from trading_ai.daemon_testing.daemon_test_scenarios import ALL_SCENARIOS
    from trading_ai.daemon_testing.registry import load_daemon_avenue_bindings

    avs = load_daemon_avenue_bindings(runtime_root=tmp_path)
    av = avs[0]
    g = av.gates[0]
    sc = ALL_SCENARIOS[0]
    row = build_fake_row(
        avenue=av,
        gate=g,
        scenario_id=sc.scenario_id,
        scenario_title=sc.title,
        execution_mode="supervised_live",
    )
    assert_autonomous_never_proven_from_fake(row)


def test_avenue_gate_pairs_cover_execution_routes() -> None:
    pairs = list(iter_avenue_gate_pairs())
    aids = {a.avenue_id for a, _ in pairs}
    assert "A" in aids and "B" in aids and "C" in aids
