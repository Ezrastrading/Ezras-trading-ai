"""Automation heartbeat registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.ops.automation_heartbeat import heartbeat_status, record_heartbeat


def test_heartbeat_ok_and_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    record_heartbeat("morning_cycle", ok=True, note="t", expected_interval_minutes=60)
    st = heartbeat_status()
    assert st["overall"] in ("healthy", "degraded")
    comps = {c["component"]: c for c in st["components"]}
    assert comps["morning_cycle"]["status"] == "OK"


def test_heartbeat_stale_old_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    sp = runtime_root() / "state" / "automation_heartbeat_state.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        json.dumps(
            {
                "version": 1,
                "heartbeats": {
                    "evening_cycle": {
                        "last_seen_at": "2020-01-01T00:00:00+00:00",
                        "status": "OK",
                        "expected_interval_minutes": 60,
                        "note": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    st = heartbeat_status()
    comps = {c["component"]: c for c in st["components"]}
    assert comps["evening_cycle"]["status"] == "STALE"
    assert "evening_cycle" in st.get("stale_or_unknown_components", [])
