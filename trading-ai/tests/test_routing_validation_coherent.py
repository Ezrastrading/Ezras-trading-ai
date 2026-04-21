"""Coherent validation resolution: success vs blocked is never ambiguous."""

from __future__ import annotations

from unittest.mock import patch

from trading_ai.nte.execution.routing.integration.validation_resolve import (
    resolve_validation_product_coherent,
    tuple_for_legacy_api,
)


def _usd_row(val: str) -> dict:
    return {
        "currency": "USD",
        "uuid": "u1",
        "available_balance": {"value": val, "currency": "USD"},
        "balance": {"value": val, "currency": "USD"},
    }


def test_coherent_success_matches_tuple_legacy() -> None:
    class FakeClient:
        def list_all_accounts(self):
            return [_usd_row("50")]

    with patch(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        return_value=True,
    ):
        vr = resolve_validation_product_coherent(FakeClient(), quote_notional=10.0)  # type: ignore[arg-type]
    assert vr.resolution_status == "success"
    assert vr.chosen_product_id == "BTC-USD"
    assert vr.error_code is None
    pid, diag, err = tuple_for_legacy_api(vr)
    assert pid == "BTC-USD" and err is None and diag.get("resolution_version") == "coherent_v6"
    assert diag.get("selector_aligned_with_guard") is True
    assert isinstance(diag.get("ordered_candidates"), list) and diag["ordered_candidates"][0] == "BTC-USD"
    assert diag.get("candidate_attempts") and diag["candidate_attempts"][0].get("priority_rank") == 1
    assert "selected:BTC-USD" in str(diag.get("final_selection_reason") or "")


def test_coherent_blocked_has_null_chosen_and_error() -> None:
    class FakeClient:
        def list_all_accounts(self):
            return []

    with patch(
        "trading_ai.runtime_proof.coinbase_accounts._product_spot_tradable_public",
        return_value=True,
    ):
        vr = resolve_validation_product_coherent(FakeClient(), quote_notional=10.0)  # type: ignore[arg-type]
    assert vr.resolution_status == "blocked"
    assert vr.chosen_product_id is None
    assert vr.error_code == "insufficient_allowed_quote_balance"
    pid, diag, err = tuple_for_legacy_api(vr)
    assert pid is None and err == "insufficient_allowed_quote_balance"
    assert str(diag.get("final_selection_reason") or "").startswith("blocked:insufficient_allowed_quote_balance")
