"""Canonical runtime Coinbase product policy + validation coherence."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from trading_ai.nte.config.settings import NTECoinbaseSettings
from trading_ai.nte.execution.routing.integration.validation_resolve import (
    _determine_blocked_error,
    resolve_validation_product_coherent,
)
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
    resolve_coinbase_runtime_product_policy,
)
from trading_ai.nte.hardening.coinbase_product_policy import (
    default_live_validation_product_priority,
    ordered_validation_candidates,
)


def test_live_validation_product_priority_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_VALIDATION_PRODUCT_PRIORITY", '["ETH-USD","BTC-USD"]')
    assert default_live_validation_product_priority() == ("ETH-USD", "BTC-USD")


def test_env_override_removing_defaults_emits_warning(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("NTE_PRODUCTS", "BTC-USD,ETH-USD")
    caplog.set_level(logging.WARNING)
    resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    assert any(
        "WARNING: runtime policy removed default product BTC-USDC via env override" in rec.message
        for rec in caplog.records
    )


def test_runtime_policy_empty_allowlist_is_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _empty_products() -> NTECoinbaseSettings:
        return replace(NTECoinbaseSettings(), products=tuple())

    monkeypatch.setattr(
        "trading_ai.nte.execution.routing.policy.runtime_coinbase_policy.load_nte_settings",
        _empty_products,
    )
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    assert pol.runtime_allowlist_valid is False
    assert pol.runtime_allowlist_error_code == "runtime_policy_empty_or_invalid"


def test_resolve_coinbase_runtime_product_policy_includes_btc_usdc_in_defaults() -> None:
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    assert "BTC-USDC" in pol.configured_default_products
    assert "BTC-USDC" in pol.runtime_active_products
    assert "BTC-USDC" in pol.validation_allowed_products


def test_gate_b_report_includes_same_canonical_coinbase_policy_shape() -> None:
    from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report

    r = gate_b_live_status_report()
    pol = r.get("coinbase_single_leg_runtime_policy") or {}
    assert "runtime_active_products" in pol
    assert "validation_active_products" in pol
    assert isinstance(r.get("validation_active_products"), list)
    assert isinstance(r.get("execution_active_products"), list)
    assert "gate_b_disabled_by_operator_state" in r
    assert "gate_b_disabled_by_runtime_policy" in r


def test_gate_a_and_gate_b_policy_functions_share_runtime_active_products() -> None:
    """Same resolver as Gate bundle — no duplicated allowlist source."""
    a = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    b = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
    assert a.runtime_active_products == b.runtime_active_products
    assert a.validation_allowed_products == [
        p for p in ordered_validation_candidates() if p.upper() in {x.upper() for x in a.runtime_active_products}
    ]


def test_validation_success_usdc_when_usd_insufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    class FC:
        def list_all_accounts(self):
            return [
                {
                    "currency": "USD",
                    "available_balance": {"value": "7.01", "currency": "USD"},
                },
                {
                    "currency": "USDC",
                    "available_balance": {"value": "20.35", "currency": "USDC"},
                },
            ]

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert vr.resolution_status == "success"
    assert vr.chosen_product_id == "BTC-USDC"
    assert vr.error_code is None


def test_validation_blocked_fundable_disallowed_explicit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class FC:
        def list_all_accounts(self):
            return [
                {
                    "currency": "USDC",
                    "available_balance": {"value": "25", "currency": "USDC"},
                },
            ]

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "trading_ai.nte.hardening.coinbase_product_policy.coinbase_product_nte_allowed",
        lambda p: p.upper() == "BTC-USD",
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert vr.resolution_status == "blocked"
    assert vr.chosen_product_id is None
    assert vr.error_code == "runtime_policy_disallows_fundable_product"
    assert "runtime policy disallows" in (vr.diagnostics or {}).get("operator_message_plain_english", "")


def test_blocked_no_contradictory_chosen_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FC:
        def list_all_accounts(self):
            return []

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    out = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert out.chosen_product_id is None
    assert out.error_code == "insufficient_allowed_quote_balance"


def test_venue_minimum_escalates_required_quote_over_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """required_quote = max(requested_notional, venue_min); USD 9 cannot cover BTC-USD min 10 at request 10."""

    class FC:
        def list_all_accounts(self):
            return [
                {
                    "currency": "USD",
                    "available_balance": {"value": "9.0", "currency": "USD"},
                },
                {
                    "currency": "USDC",
                    "available_balance": {"value": "25", "currency": "USDC"},
                },
            ]

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert vr.resolution_status == "success"
    assert vr.chosen_product_id == "BTC-USDC"
    attempts = (vr.diagnostics or {}).get("candidate_attempts") or []
    usd_row = next((a for a in attempts if a.get("product_id") == "BTC-USD"), {})
    assert usd_row.get("quote_sufficient") is False
    assert float(usd_row.get("quote_required") or 0) >= 10.0


def test_candidate_attempts_record_status_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    class FC:
        def list_all_accounts(self):
            return []

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    row0 = (vr.diagnostics or {}).get("candidate_attempts", [{}])[0]
    assert row0.get("status") == "rejected"
    assert row0.get("detail")
    assert row0.get("reason_code")


def test_fallback_error_uses_no_runtime_supported_when_ticker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticker fails for all quote-sufficient allowed candidates → no_runtime_supported_validation_product."""

    class FC:
        def list_all_accounts(self):
            return [
                {
                    "currency": "USD",
                    "available_balance": {"value": "100", "currency": "USD"},
                },
            ]

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: False,
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert vr.error_code == "no_runtime_supported_validation_product"


def test_venue_min_notional_binding_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Balance covers requested notional but not max(requested, venue_min) → venue_min_notional_not_fundable."""

    monkeypatch.setattr(
        "trading_ai.nte.execution.routing.integration.validation_resolve.venue_min_notional_usd",
        lambda _pid: 50.0,
    )

    class FC:
        def list_all_accounts(self):
            return [
                {
                    "currency": "USD",
                    "available_balance": {"value": "40", "currency": "USD"},
                },
            ]

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        lambda _pid: True,
    )
    vr = resolve_validation_product_coherent(FC(), quote_notional=10.0)
    assert vr.resolution_status == "blocked"
    assert vr.chosen_product_id is None
    assert vr.error_code == "venue_min_notional_not_fundable"
    assert (vr.diagnostics or {}).get("root_cause_primary") == "venue_min_notional_not_fundable"
    assert (vr.diagnostics or {}).get("funding_truth_classification")


def test_determine_blocked_error_fallback_is_no_allowed_validation_product_found() -> None:
    attempts = [
        {
            "product_id": "BTC-USD",
            "runtime_allowed": True,
            "quote_sufficient": True,
            "venue_supported": True,
            "rejection_reason": "unexpected_edge",
        },
    ]
    code, _h, _n = _determine_blocked_error(attempts)
    assert code == "no_allowed_validation_product_found"
