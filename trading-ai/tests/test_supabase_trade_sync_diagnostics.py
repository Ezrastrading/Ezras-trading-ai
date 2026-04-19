"""Supabase trade sync diagnostics (no live network in default tests)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_ai.nte.databank import supabase_trade_sync as sts


def test_report_supabase_trade_sync_diagnostics_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    r = sts.report_supabase_trade_sync_diagnostics()
    assert r["supabase_url_present"] is False
    assert r["key_source_used"] == "none"
    assert r["client_init_ok"] is False
    assert r["insert_probe_ok"] is False


def test_report_supabase_trade_sync_diagnostics_key_precedence_and_probe_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "k1")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k2")

    def fake_create_client(url: str, key: str) -> MagicMock:
        c = MagicMock()
        c.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        c.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock()
        return c

    pytest.importorskip("supabase")
    import supabase as supabase_mod

    monkeypatch.setattr(supabase_mod, "create_client", fake_create_client)

    r = sts.report_supabase_trade_sync_diagnostics()
    assert r["supabase_url_present"] is True
    assert r["key_source_used"] == "SUPABASE_KEY"
    assert r["client_init_ok"] is True
    assert r["insert_probe_ok"] is True
