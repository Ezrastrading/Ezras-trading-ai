"""Supabase trade sync: retries, local fallback queue, flush."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_ai.nte.databank import supabase_trade_sync as sts


def test_upsert_failure_queues_row_and_flush_replays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("supabase")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setattr(sts.time, "sleep", lambda _s: None)

    attempts = {"n": 0}

    def fake_create_client(url: str, key: str) -> MagicMock:
        c = MagicMock()

        def _execute() -> MagicMock:
            attempts["n"] += 1
            raise RuntimeError("simulated API failure")

        c.table.return_value.upsert.return_value.execute.side_effect = _execute
        return c

    import supabase as supabase_mod

    monkeypatch.setattr(supabase_mod, "create_client", fake_create_client)
    monkeypatch.setattr(sts, "verify_trade_exists", lambda tid: True)

    row = {"trade_id": "fb_1", "avenue_name": "coinbase", "schema_version": "1.0.0"}
    out = sts.upsert_trade_event(row)
    assert out["write_status"] == "failed"
    assert out["success"] is False
    assert out["attempts"] == 5
    assert out["queued_locally"] is True

    q = sts.local_unsynced_trades_path()
    assert q.is_file()
    lines = q.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["trade_id"] == "fb_1"

    attempts["n"] = 0
    ok_client = MagicMock()
    ok_client.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(supabase_mod, "create_client", lambda u, k: ok_client)

    flushed = sts.flush_unsynced_trades()
    assert flushed["flushed"] == 1
    assert flushed["remaining"] == 0
    assert not q.is_file()


def test_flush_keeps_row_when_upsert_still_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("supabase")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setattr(sts.time, "sleep", lambda _s: None)

    q = sts.local_unsynced_trades_path()
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(json.dumps({"trade_id": "fb_2", "schema_version": "1.0.0"}) + "\n", encoding="utf-8")

    def fake_create_client(url: str, key: str) -> MagicMock:
        c = MagicMock()
        c.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("still down")
        return c

    import supabase as supabase_mod

    monkeypatch.setattr(supabase_mod, "create_client", fake_create_client)
    monkeypatch.setattr(sts, "verify_trade_exists", lambda tid: True)

    flushed = sts.flush_unsynced_trades()
    assert flushed["flushed"] == 0
    assert flushed["remaining"] == 1
    assert q.is_file()
