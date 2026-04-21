"""Spot routing package smoke — types + wallet row helper."""

from __future__ import annotations

from trading_ai.nte.execution.spot_routing import Route, SpotProductRef, build_wallet_inventory_rows
from trading_ai.nte.execution.spot_routing.types import route_quality_stub


def test_route_is_sequence_of_real_products() -> None:
    r = Route(legs=(SpotProductRef("BTC-USDC", "BTC", "USDC"),))
    assert r.as_product_ids() == ["BTC-USDC"]


def test_route_quality_stub() -> None:
    assert route_quality_stub().get("note")


def test_build_wallet_inventory_rows_mock() -> None:
    class C:
        def list_all_accounts(self):
            return [
                {"currency": "USD", "available_balance": {"value": "5", "currency": "USD"}},
            ]

    rows = build_wallet_inventory_rows(C())  # type: ignore[arg-type]
    assert any(r.get("currency") == "USD" for r in rows)
