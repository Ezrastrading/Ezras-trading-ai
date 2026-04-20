"""Universal live guard: registry, Coinbase assert path, shark outlets, halt, duplicate short-circuit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.shark.models import ExecutionIntent


def test_registered_coinbase_gate_b_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.safety.universal_live_guard import reset_universal_live_guard_metrics_for_tests, run_universal_live_guard_precheck

    reset_universal_live_guard_metrics_for_tests()
    out = run_universal_live_guard_precheck("coinbase", "gate_b", check_execution_halt=False, runtime_root=tmp_path)
    assert out["universal_live_guard_allowed"] is True
    assert out["universal_live_guard_registry_hit"] is True
    assert (tmp_path / "data" / "control" / "universal_live_guard_last_eval.json").is_file()


def test_unregistered_denied() -> None:
    from trading_ai.safety.universal_live_guard import evaluate_universal_live_guard

    ok, reason, det = evaluate_universal_live_guard("not_a_real_venue_xx", "gate_a", fail_closed=True)
    assert ok is False
    assert "unregistered" in reason
    assert det.get("universal_live_guard_registry_hit") is False


def test_halt_blocks_when_kill_switch_truth_halted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    halt = {
        "halted": True,
        "kill_switch_reason_code": "TEST",
        "severity": "CRITICAL",
        "source_component": "test",
        "immediate_action_required": "x",
        "halt_timestamp": "t",
        "detail": {},
    }
    import json

    (tmp_path / "data" / "control" / "kill_switch_truth.json").write_text(json.dumps(halt), encoding="utf-8")
    from trading_ai.safety.universal_live_guard import run_universal_live_guard_precheck

    with pytest.raises(RuntimeError):
        run_universal_live_guard_precheck("coinbase", "gate_a", check_execution_halt=True, runtime_root=tmp_path)


def test_duplicate_short_circuit_same_sig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.safety.universal_live_guard import reset_universal_live_guard_metrics_for_tests, run_universal_live_guard_precheck

    reset_universal_live_guard_metrics_for_tests()
    a = run_universal_live_guard_precheck("coinbase", "gate_a", trade_id="t1", check_execution_halt=False, runtime_root=tmp_path)
    b = run_universal_live_guard_precheck("coinbase", "gate_a", trade_id="t1", check_execution_halt=False, runtime_root=tmp_path)
    assert b.get("universal_live_guard_duplicate_short_circuit") is True
    assert a["universal_live_guard_allowed"] == b["universal_live_guard_allowed"]


def test_live_order_guard_source_wires_precheck() -> None:
    import inspect

    from trading_ai.nte.hardening import live_order_guard

    src = inspect.getsource(live_order_guard.assert_live_order_permitted)
    assert "run_universal_live_guard_precheck" in src


def test_shark_kalshi_guard_returns_order_result_on_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.shark.execution_live import _universal_live_guard_shark_block

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("universal_live_guard_unregistered:kalshi|gate_b")

    with patch("trading_ai.safety.universal_live_guard.run_universal_live_guard_precheck", side_effect=boom):
        intent = ExecutionIntent(
            market_id="KX-TEST",
            side="yes",
            stake_fraction_of_capital=0.01,
            edge_after_fees=0.01,
            estimated_win_probability=0.5,
            hunt_types=[],
            source="test",
            shares=1,
            outlet="kalshi",
            meta={"client_order_id": "c1"},
        )
        res = _universal_live_guard_shark_block(intent, outlet="kalshi", gate="gate_b")
        assert res is not None
        assert res.success is False
        assert "universal_live_guard" in (res.status or "")
