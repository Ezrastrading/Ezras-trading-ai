"""Coinbase avenue enablement — single env truth (COINBASE_EXECUTION_ENABLED or COINBASE_ENABLED)."""

from __future__ import annotations

import pytest


def test_coinbase_avenue_enabled_via_execution_flag_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COINBASE_ENABLED", raising=False)
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    from trading_ai.nte.hardening.mode_context import coinbase_avenue_execution_enabled, get_mode_context

    assert coinbase_avenue_execution_enabled() is True
    assert get_mode_context().coinbase_enabled is True


def test_coinbase_avenue_enabled_via_legacy_coinbase_enabled_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COINBASE_EXECUTION_ENABLED", raising=False)
    monkeypatch.setenv("COINBASE_ENABLED", "true")
    from trading_ai.nte.hardening.mode_context import coinbase_avenue_execution_enabled, get_mode_context

    assert coinbase_avenue_execution_enabled() is True
    assert get_mode_context().coinbase_enabled is True


def test_describe_coinbase_lists_decision_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    from trading_ai.nte.hardening.mode_context import describe_coinbase_avenue_enablement

    d = describe_coinbase_avenue_enablement()
    assert d.get("coinbase_avenue_enabled") is True
    assert "COINBASE_EXECUTION_ENABLED" in (d.get("decision_env_keys") or [])
