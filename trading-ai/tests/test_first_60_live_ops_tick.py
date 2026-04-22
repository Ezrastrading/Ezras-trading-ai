"""First-60 live ops automation tick."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_run_first_60_live_ops_tick_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_LIVE_START_DATE", "2026-01-01")
    from trading_ai.control.first_60_day_ops import run_first_60_live_ops_tick

    r1 = run_first_60_live_ops_tick(force=True)
    assert r1.get("ok") is True
    hb = tmp_path / "data" / "control" / "first_60_live_ops_heartbeat.json"
    assert hb.is_file()
    env_p = tmp_path / "data" / "review" / "first_60_day_daily_envelope.json"
    assert env_p.is_file()
    body = json.loads(env_p.read_text(encoding="utf-8"))
    assert body.get("truth_version") == "first_60_day_daily_envelope_v1"
    r2 = run_first_60_live_ops_tick(force=False)
    assert r2.get("daily", {}).get("skipped") is True
