"""User-stream message normalization (no live socket)."""

from __future__ import annotations

from trading_ai.nte.data.ws_user_feed import normalize_user_channel_message


def test_normalize_order_partial_and_full():
    payload = {
        "channel": "user",
        "events": [
            {
                "type": "update",
                "orders": [
                    {
                        "order_id": "o1",
                        "product_id": "BTC-USD",
                        "side": "BUY",
                        "status": "OPEN",
                        "filled_size": "0.001",
                        "base_size": "0.01",
                    }
                ],
            }
        ],
    }
    rows = normalize_user_channel_message(payload)
    assert len(rows) == 1
    assert rows[0]["lifecycle"] == "partially_filled"
    assert rows[0]["order_id"] == "o1"


def test_normalize_exit_sell_filled_sets_exit_flag():
    payload = {
        "channel": "user",
        "events": [
            {
                "orders": [
                    {
                        "order_id": "o2",
                        "product_id": "ETH-USD",
                        "side": "SELL",
                        "status": "FILLED",
                        "filled_size": "0.5",
                        "base_size": "0.5",
                    }
                ],
            }
        ],
    }
    rows = normalize_user_channel_message(payload)
    assert rows[0]["exit_filled_confirmed"] is True
