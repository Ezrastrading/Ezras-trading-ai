"""Supervised daemon: duplicate trade window is an expected skip, not a failure."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status, run_avenue_a_daemon_once
from trading_ai.storage.storage_adapter import LocalStorageAdapter


@pytest.fixture
def supervised_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "supervised_live")
    return tmp_path


def test_supervised_duplicate_window_skip_not_failure(supervised_runtime):
    root = supervised_runtime
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(
        "data/control/avenue_a_daemon_state.json",
        {"consecutive_autonomous_ok_cycles": 7},
    )
    ad.write_json(
        "data/control/runtime_runner_last_failure.json",
        {"stub": "unchanged_marker", "ts": "2020-01-01T00:00:00Z"},
    )

    with patch("trading_ai.orchestration.runtime_runner.daemon_abort_conditions", return_value=(False, "", False)):
        with patch(
            "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_supervised_runtime_allowed",
            return_value=(True, "ok"),
        ):
            with patch(
                "trading_ai.orchestration.avenue_a_live_daemon._rebuy_allows_next_entry",
                return_value=(True, ""),
            ):
                with patch(
                    "trading_ai.safety.failsafe_guard.peek_duplicate_trade_window_would_block_entry",
                    return_value=True,
                ) as peek:
                    with patch(
                        "trading_ai.runtime_proof.live_execution_validation.run_single_live_execution_validation",
                    ) as run_live:
                        out = run_avenue_a_daemon_once(runtime_root=root, product_id="BTC-USD")

    assert out["ok"] is True
    assert out.get("skipped") is True
    assert out.get("skip_reason") == "duplicate_trade_window_active"
    assert out.get("skip_classification") == "expected_guard_skip"
    assert out.get("live_orders") is False
    peek.assert_called_once()
    run_live.assert_not_called()

    st = ad.read_json("data/control/avenue_a_daemon_state.json") or {}
    assert int(st.get("consecutive_autonomous_ok_cycles") or 0) == 7

    fail_path = root / "data/control/runtime_runner_last_failure.json"
    assert json.loads(fail_path.read_text(encoding="utf-8")).get("stub") == "unchanged_marker"

    cycle = ad.read_json("data/control/runtime_runner_last_cycle.json") or {}
    assert cycle.get("runtime_root") == str(root.resolve())
    inner = cycle.get("avenue_a_daemon") or {}
    assert inner.get("skip_reason") == "duplicate_trade_window_active"


def test_avenue_a_daemon_status_supervised_green_when_readiness_ok(supervised_runtime):
    root = supervised_runtime
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(
        "data/control/avenue_a_daemon_state.json",
        {
            "last_supervised_cycle_ok": True,
            "last_supervised_cycle_skipped": True,
            "last_supervised_skip_reason": "duplicate_trade_window_active",
            "last_supervised_live_order_attempted": False,
        },
    )

    with patch(
        "trading_ai.orchestration.avenue_a_daemon_policy.avenue_a_supervised_runtime_allowed",
        return_value=(True, "ok"),
    ):
        st = avenue_a_daemon_status(runtime_root=root)

    sup = st.get("supervised") or {}
    assert sup.get("can_run_supervised_now") is True
    assert sup.get("supervised_blockers_if_false") == []
    assert sup.get("last_supervised_cycle_ok") is True
    assert sup.get("last_supervised_cycle_skipped") is True
    assert sup.get("last_supervised_skip_reason") == "duplicate_trade_window_active"
    assert sup.get("last_supervised_live_order_attempted") is False
    aut = st.get("autonomous") or {}
    assert "dual_gate_blockers_if_false" in aut
