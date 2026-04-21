from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_supervisor_runs_multiple_loops_repeatedly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    a = run_role_supervisor_once(role="ops", runtime_root=tmp_path, force_all_due=True)
    b = run_role_supervisor_once(role="ops", runtime_root=tmp_path, force_all_due=True)
    assert a.get("ok") is True and b.get("ok") is True
    assert len(a.get("ran") or []) >= 3

    stp = tmp_path / "data" / "control" / "operating_system" / "loop_status_ops.json"
    st = json.loads(stp.read_text(encoding="utf-8"))
    loops = st.get("loops") or {}
    assert "scanner_cycle" in loops
    assert "outcome_ingestion" in loops

    # Per-loop result artifacts exist.
    loop_file = tmp_path / "data" / "control" / "operating_system" / "loops" / "ops" / "scanner_cycle.json"
    assert loop_file.is_file()


def test_research_supervisor_emits_comparisons_and_pnl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    out = run_role_supervisor_once(role="research", runtime_root=tmp_path, skip_models=True, force_all_due=True)
    assert out.get("ok") is True
    ran = set(out.get("ran") or [])
    assert "pnl_review" in ran
    assert "comparisons" in ran

