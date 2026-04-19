"""Supabase JWT resolution — SUPABASE_KEY preferred over SUPABASE_SERVICE_ROLE_KEY."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key


def test_prefers_supabase_key_over_service_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_KEY", "key_a")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key_b")
    k, src = resolve_supabase_jwt_key()
    assert k == "key_a"
    assert src == "SUPABASE_KEY"


def test_falls_back_to_service_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_only")
    k, src = resolve_supabase_jwt_key()
    assert k == "svc_only"
    assert src == "SUPABASE_SERVICE_ROLE_KEY"


def test_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    k, src = resolve_supabase_jwt_key()
    assert k is None
    assert src == "none"
