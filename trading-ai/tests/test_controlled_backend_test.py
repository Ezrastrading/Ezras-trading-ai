"""Controlled backend validation suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.ops import controlled_backend_test as cbt


def test_controlled_backend_test_passes_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EZRAS_CONTROLLED_TEST_IN_PLACE", raising=False)
    out = cbt.run_controlled_backend_test(isolated=True, runtime_root_override=tmp_path)
    assert out.get("ok") is True
    assert out.get("status") == "PASS"
    assert out.get("ready_for_first_real_supervised_trade") is True
    rp = out.get("report_path") or ""
    assert Path(rp).is_file()
    assert "Controlled Backend Test Report" in Path(rp).read_text(encoding="utf-8")
    ids = [s.get("id") for s in out.get("scenarios") or []]
    assert "scenario_1_governance_boot" in ids
    assert "scenario_11_final_consistency_integrity_recheck" in ids


def test_scenario_ids_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EZRAS_CONTROLLED_TEST_IN_PLACE", raising=False)
    out = cbt.run_controlled_backend_test(isolated=True, runtime_root_override=tmp_path)
    assert len(out.get("scenarios") or []) == 11
