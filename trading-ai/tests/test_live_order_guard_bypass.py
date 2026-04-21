"""Live-order guard cannot be bypassed via outlet surface or raw POST /orders."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture()
def live_trade_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("NTE_PAPER_MODE", "false")
    monkeypatch.setenv("NTE_DRY_RUN", "false")
    monkeypatch.setenv("COINBASE_ENABLED", "true")
    monkeypatch.setenv("NTE_EXECUTION_SCOPE", "live")
    monkeypatch.setenv("NTE_COINBASE_EXECUTION_ROUTE", "live")


def test_place_market_buy_blocked_paper_even_if_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    c = CoinbaseClient()
    with patch.object(c, "_order_guarded_request", wraps=c._order_guarded_request):
        r = c.place_market_buy("BTC-USD", 10.0)
    assert r.success is False
    assert "blocked" in (r.reason or "").lower() or "Live order" in (r.reason or "")


def test_raw_request_post_orders_blocked_without_guard(live_trade_env, monkeypatch):
    """Direct ``_request(POST,/orders)`` must raise — use public APIs only."""
    monkeypatch.delenv("NTE_PAPER_MODE", raising=False)
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    c = CoinbaseClient()
    with patch.object(c, "_credentials_ready", return_value=True):
        with patch.object(c, "_build_jwt_with_uri_claim", return_value="fake"):
            with pytest.raises(RuntimeError, match="use place_market"):
                c._request("POST", "/orders", body={"client_order_id": "x", "product_id": "BTC-USD"})


def test_cancel_order_blocked_paper(monkeypatch, tmp_path):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    c = CoinbaseClient()
    assert c.cancel_order("fake-oid") is False


def test_fallback_limit_retry_uses_guarded_path(monkeypatch, tmp_path):
    """``place_limit_gtc`` uses ``_order_guarded_request`` (not raw ``_request``)."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("NTE_PAPER_MODE", "false")
    monkeypatch.setenv("NTE_DRY_RUN", "false")
    monkeypatch.setenv("COINBASE_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("NTE_EXECUTION_SCOPE", "live")
    monkeypatch.setenv("NTE_COINBASE_EXECUTION_ROUTE", "live")
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    monkeypatch.setenv("EZRAS_CONTROL_ARTIFACT_PREFLIGHT", "false")
    monkeypatch.setenv("GAP_MIN_CONFIDENCE_SCORE", "0.0")
    monkeypatch.setenv("GAP_MIN_EDGE_PERCENT", "-9999")
    monkeypatch.setenv("GAP_MIN_LIQUIDITY_SCORE", "0.0")
    from trading_ai.global_layer.gap_models import (
        authoritative_live_buy_path_set,
        authoritative_live_buy_path_reset,
        candidate_context_set,
        candidate_context_reset,
    )
    from trading_ai.nte.paths import nte_system_health_path
    from trading_ai.nte.utils.atomic_json import atomic_write_json

    atomic_write_json(
        nte_system_health_path(),
        {"healthy": True, "execution_should_pause": False, "global_pause": False},
    )
    from trading_ai.shark.mission import mission_probability_reset, mission_probability_set
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    cand = {
        "candidate_id": "ugc_bypass",
        "edge_percent": 10.0,
        "edge_score": 10.0,
        "confidence_score": 0.9,
        "execution_mode": "maker",
        "gap_type": "probability_gap",
        "estimated_true_value": 100.0,
        "liquidity_score": 0.9,
        "fees_estimate": 0.01,
        "slippage_estimate": 0.01,
        "must_trade": True,
        "risk_flags": [],
    }
    ct = candidate_context_set(cand)  # type: ignore[arg-type]
    at = authoritative_live_buy_path_set("nte_only")
    pt = mission_probability_set(0.85)
    c = CoinbaseClient()
    calls: list = []

    def track(method, path, **kwargs):
        calls.append((method, path))
        return {"success": False, "error_response": {"message": "test"}}

    try:
        with patch(
            "trading_ai.nte.hardening.live_order_guard._coinbase_credentials_ready",
            return_value=True,
        ):
            with patch.object(c, "_credentials_ready", return_value=True):
                with patch.object(c, "_order_guarded_request", side_effect=track):
                    c.place_limit_gtc("BTC-USD", "BUY", "0.001", "90000", post_only=True)
    finally:
        try:
            mission_probability_reset(pt)
        except Exception:
            pass
        try:
            candidate_context_reset(ct)
        except Exception:
            pass
        try:
            authoritative_live_buy_path_reset(at)
        except Exception:
            pass
    assert any(x == ("POST", "/orders") for x in calls)


def test_emergency_exit_path_not_blocked_by_global_pause(monkeypatch, tmp_path):
    """Exit actions bypass execution pause in live_order_guard."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("NTE_PAPER_MODE", "false")
    monkeypatch.setenv("NTE_DRY_RUN", "false")
    monkeypatch.setenv("COINBASE_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("NTE_EXECUTION_SCOPE", "live")
    monkeypatch.setenv("NTE_COINBASE_EXECUTION_ROUTE", "live")
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    monkeypatch.setenv("EZRAS_CONTROL_ARTIFACT_PREFLIGHT", "false")
    from trading_ai.nte.paths import nte_system_health_path
    from trading_ai.nte.utils.atomic_json import atomic_write_json

    p = nte_system_health_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        p,
        {
            "execution_should_pause": True,
            "healthy": False,
            "global_pause": True,
        },
    )
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    c = CoinbaseClient()
    with patch(
        "trading_ai.nte.hardening.live_order_guard._coinbase_credentials_ready",
        return_value=True,
    ):
        with patch.object(c, "_credentials_ready", return_value=True):
            with patch.object(
                c,
                "_order_guarded_request",
                return_value={"success": True, "success_response": {"order_id": "x"}},
            ):
                r = c.place_market_sell("BTC-USD", "0.0001")
    assert r.success is True


def test_coinbase_fetcher_orders_respect_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    from trading_ai.shark.outlets.coinbase import CoinbaseFetcher

    out = CoinbaseFetcher.place_market_order("BTC-USD", "buy", "0.001")
    assert out.get("ok") is False
