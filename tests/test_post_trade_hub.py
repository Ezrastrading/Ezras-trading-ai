"""Post-trade hub validation (no Telegram when unconfigured)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.automation import post_trade_hub as hub


def test_validate_placed_requires_trade_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = hub.execute_post_trade_placed(None, {"market": "x"})
    assert out.get("status") == "failed"
    assert "trade_id" in (out.get("error") or "").lower() or out.get("error")


def test_validate_closed_requires_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = hub.execute_post_trade_closed(
        None,
        {"trade_id": "t1", "capital_allocated": 10.0, "roi_percent": 1.0},
    )
    assert out.get("status") == "failed"


def test_manifest_written_after_placed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "hub-t1",
        "timestamp": "2026-04-13T10:00:00+00:00",
        "market": "M",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 10.0,
        "signal_score": 5,
        "expected_value": 0.01,
        "event_name": "e",
    }
    hub.execute_post_trade_placed(None, t)
    mp = tmp_path / "state" / "post_trade_manifest.json"
    assert mp.is_file()
    data = json.loads(mp.read_text(encoding="utf-8"))
    assert data.get("last_placed", {}).get("trade_id") == "hub-t1"
