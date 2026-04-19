"""Smoke tests for NTE hardening (isolated runtime)."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture()
def nte_runtime():
    d = tempfile.mkdtemp(prefix="nte_test_")
    old = os.environ.get("EZRAS_RUNTIME_ROOT")
    os.environ["EZRAS_RUNTIME_ROOT"] = d
    os.environ["NTE_EXECUTION_MODE"] = "paper"
    os.environ.pop("NTE_LIVE_TRADING_ENABLED", None)
    yield d
    os.environ.pop("EZRAS_RUNTIME_ROOT", None)
    if old:
        os.environ["EZRAS_RUNTIME_ROOT"] = old


def test_config_validate(nte_runtime):
    from trading_ai.nte.config.config_validator import validate_nte_settings

    ok, errs = validate_nte_settings()
    assert ok
    assert errs == []


def test_integrity_scan(nte_runtime):
    from trading_ai.nte.hardening.memory_integrity_checker import run_integrity_scan

    r = run_integrity_scan()
    assert isinstance(r, list)
    assert all("file" in x for x in r)


def test_system_health_refresh(nte_runtime):
    from trading_ai.nte.reports.system_health_reporter import refresh_default_health

    h = refresh_default_health()
    assert "healthy" in h
    assert h.get("mode") == "paper"


def test_live_blocked_paper(nte_runtime):
    from trading_ai.nte.hardening.mode_guard import assert_live_order_permitted

    with pytest.raises(RuntimeError):
        assert_live_order_permitted("test")


def test_capital_ledger(nte_runtime):
    from trading_ai.nte.capital_ledger import append_realized, net_equity_estimate

    append_realized(1.0, avenue="coinbase", label="t", fees_usd=0.1)
    assert net_equity_estimate() != 0.0


def test_research_firewall(nte_runtime):
    from trading_ai.nte.research.research_firewall import promotion_allowed

    assert promotion_allowed("x", passed_checks=False) is False
    assert promotion_allowed("y", passed_checks=True) is True
