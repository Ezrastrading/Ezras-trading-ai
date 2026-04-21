"""Fill polling: order_snapshot fallback when historical fills lag (live validation)."""

from __future__ import annotations

from typing import Any, Dict, List

from trading_ai.runtime_proof.live_execution_validation import _poll_until_order_fill_rows


class _StubClientEmptyFills:
    """Fills API empty; historical order shows a completed IOC sell."""

    def get_fills(self, order_id: str) -> List[Dict[str, Any]]:
        return []

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return {
            "order_id": order_id,
            "status": "FILLED",
            "filled_size": "0.00536578",
            "filled_value": "398.50",
            "average_filled_price": "74300",
            "total_fees": "0.02",
        }


def test_poll_uses_order_snapshot_when_fills_empty() -> None:
    c = _StubClientEmptyFills()
    fills, snap, src, diag = _poll_until_order_fill_rows(
        c,
        "ee82514e5bee40098207c46c213e7669",
        product_id="BTC-USD",
        side="SELL",
        timeout_sec=3.0,
        sleep_fn=lambda _: None,
    )
    assert fills
    assert src == "order_snapshot"
    assert snap.get("status") == "FILLED"
    assert any("synthetic" in x or "order_snapshot" in x for x in diag)


class _StubClientFillsFirst:
    def get_fills(self, order_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "price": "74000",
                "size": "0.001",
                "filled_value": "74",
                "commission": "0.01",
                "side": "SELL",
            }
        ]

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return {}


def test_poll_prefers_fills_api_when_present() -> None:
    c = _StubClientFillsFirst()
    fills, _, src, _ = _poll_until_order_fill_rows(
        c,
        "38457bfe-b193-4cf3-9509-a363f389bc2e",
        product_id="BTC-USD",
        side="SELL",
        timeout_sec=2.0,
        sleep_fn=lambda _: None,
    )
    assert len(fills) == 1
    assert src == "fills_api"
