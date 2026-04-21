"""Smoke tests for live-switch closure bundle (honest defaults on empty runtime)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_write_live_switch_closure_bundle_smoke(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    out = write_live_switch_closure_bundle(
        runtime_root=tmp_path,
        trigger_surface="test",
        reason="pytest",
    )
    assert "written" in out
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    assert ad.exists("data/control/avenue_a_final_live_blockers.json")
    assert ad.exists("data/control/final_remaining_gaps_before_live.json")
    assert ad.exists("data/control/buy_sell_log_rebuy_truth.json")


def test_material_change_bridge_calls_closure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change

    called = {"n": 0}

    def fake_refresh(**kwargs):  # noqa: ANN003
        return {"ok": True}

    def fake_closure(**kwargs):  # noqa: ANN003
        called["n"] += 1
        return {"written": []}

    monkeypatch.setattr(
        "trading_ai.reports.runtime_artifact_refresh_manager.run_refresh_runtime_artifacts",
        fake_refresh,
    )
    monkeypatch.setattr(
        "trading_ai.operator_truth.live_switch_closure_bundle.write_live_switch_closure_bundle",
        fake_closure,
    )
    refresh_runtime_truth_after_material_change(runtime_root=tmp_path, reason="unit_test")
    assert called["n"] == 1
