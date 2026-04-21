"""Assertions for normalized daemon matrix rows."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.daemon_testing.contract import DaemonMatrixRow


def assert_row_has_required_keys(row: Dict[str, Any]) -> None:
    required = (
        "avenue_id",
        "gate_id",
        "scenario_id",
        "execution_mode",
        "adapter_truth_class",
        "pass_classification",
        "autonomous_live_runtime_proven",
    )
    for k in required:
        assert k in row, f"missing {k}"


def assert_autonomous_never_proven_from_fake(row: DaemonMatrixRow) -> None:
    if row.adapter_truth_class in ("fully_fake_adapter", "venue_shaped_fake_adapter", "simulated_real_artifact_replay"):
        assert row.autonomous_live_runtime_proven is False
