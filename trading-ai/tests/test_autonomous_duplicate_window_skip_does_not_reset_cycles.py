from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_autonomous_duplicate_window_skip_does_not_reset_autonomous_cycle_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Autonomous daemon cycles may be blocked by the duplicate window guard.

    This must remain enforced, but it must not reset the autonomous "consecutive ok" counters,
    because no venue order was eligible to be submitted.
    """
    root = tmp_path.resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("EZRAS_AVENUE_A_DAEMON_MODE", "autonomous_live")

    (root / "data" / "control").mkdir(parents=True)
    # Seed a non-zero streak to prove we don't reset it on expected skip.
    (root / "data" / "control" / "avenue_a_daemon_state.json").write_text(
        '{"consecutive_autonomous_ok_cycles": 2, "consecutive_autonomous_live_only_ok_cycles": 2}',
        encoding="utf-8",
    )

    dup_err = "buy_failed:Live order blocked: failsafe_blocked:duplicate_trade_guard:duplicate_trade_window"
    proof = {"execution_success": False, "FINAL_EXECUTION_PROVEN": False, "error": dup_err, "trade_id": None}

    with patch(
        "trading_ai.orchestration.runtime_runner.daemon_abort_conditions",
        return_value=(False, "ok", False),
    ), patch(
        "trading_ai.orchestration.avenue_a_live_daemon.avenue_a_autonomous_live_allowed",
        return_value=(True, "ok"),
    ), patch(
        "trading_ai.orchestration.autonomous_daemon_live_contract.autonomous_daemon_may_submit_live_orders",
        return_value=(True, []),
    ), patch(
        "trading_ai.orchestration.avenue_a_live_daemon._rebuy_allows_next_entry",
        return_value=(True, "ok"),
    ), patch(
        "trading_ai.runtime_proof.live_execution_validation.run_single_live_execution_validation",
        return_value=proof,
    ):
        from trading_ai.orchestration.avenue_a_live_daemon import run_avenue_a_daemon_once

        out = run_avenue_a_daemon_once(runtime_root=root, quote_usd=10.0, product_id="BTC-USD", include_runtime_stability=False)

    assert out.get("ok") is True
    assert out.get("skipped") is True
    assert out.get("skip_reason") == "duplicate_trade_window_active"

    # Counters should remain unchanged (not reset to 0).
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    st = LocalStorageAdapter(runtime_root=root).read_json("data/control/avenue_a_daemon_state.json") or {}
    assert st.get("consecutive_autonomous_ok_cycles") == 2
    assert st.get("consecutive_autonomous_live_only_ok_cycles") == 2

