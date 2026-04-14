"""Post-trade hub: validation and manifest (isolated runtime dir)."""

from __future__ import annotations

import json
from pathlib import Path

from trading_ai.automation import post_trade_hub as hub


def test_placed_requires_trade_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = hub.execute_post_trade_placed(None, {"market": "x"})
    assert out.get("status") == "failed"
    assert out.get("error")


def test_closed_requires_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = hub.execute_post_trade_closed(
        None,
        {"trade_id": "t1", "capital_allocated": 10.0},
    )
    assert out.get("status") == "failed"


def test_placed_enriches_sizing_meta_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "hub-sz",
        "timestamp": "2026-04-13T10:00:00+00:00",
        "market": "M",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 100.0,
        "signal_score": 5,
        "expected_value": 0.01,
        "event_name": "e",
    }
    out = hub.execute_post_trade_placed(None, t)
    assert out.get("position_sizing") is not None
    assert out.get("position_sizing", {}).get("approved_size") == 100.0
    assert t.get("risk_bucket_at_open") == "NORMAL"


def test_placed_preserves_existing_position_sizing_meta(monkeypatch, tmp_path: Path) -> None:
    """Partial hub meta is canonicalized (full keys; economics from capital + risk state)."""
    import json

    from trading_ai.automation.position_sizing_policy import CANONICAL_META_REQUIRED_KEYS, meta_is_complete
    from trading_ai.automation.risk_bucket import risk_state_path

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = risk_state_path()
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 98.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win", "loss", "loss"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    meta = {
        "requested_size": 200.0,
        "approved_size": 100.0,
        "effective_bucket": "REDUCED",
        "raw_bucket": "REDUCED",
        "bucket_fallback_applied": False,
        "approval_status": "REDUCED",
        "reason": "risk_bucket_reduction",
    }
    t = {
        "trade_id": "hub-keep",
        "timestamp": "2026-04-13T10:00:00+00:00",
        "market": "M",
        "position": "YES",
        "entry_price": 0.5,
        "capital_allocated": 200.0,
        "signal_score": 5,
        "event_name": "e",
        "position_sizing_meta": meta,
    }
    out = hub.execute_post_trade_placed(None, t)
    ps = out.get("position_sizing") or {}
    assert meta_is_complete(ps)
    for k in CANONICAL_META_REQUIRED_KEYS:
        assert k in ps
    assert float(ps["approved_size"]) == 100.0
    assert float(ps["requested_size"]) == 200.0
    assert t["capital_allocated"] == 200.0
    assert t.get("risk_bucket_at_open") == "REDUCED"


def test_manifest_after_placed(monkeypatch, tmp_path: Path) -> None:
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
    assert data.get("last_event", {}).get("trade_id") == "hub-t1"


def test_closed_includes_execution_close_reconciliation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = hub.execute_post_trade_closed(
        None,
        {"trade_id": "close-rc", "result": "win", "roi_percent": 2.0, "capital_allocated": 50.0},
    )
    assert out.get("execution_close_reconciliation", {}).get("ok") is True
