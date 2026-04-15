"""Kalshi orderbook best-ask parsing (HV aggressive limits)."""

from __future__ import annotations

from trading_ai.shark.outlets.kalshi import KalshiClient, parse_orderbook_yes_no_best_ask_cents


def test_parse_best_asks_min_price_per_side():
    ob = {
        "orderbook": {
            "yes": [[50, 10], [52, 5]],
            "no": [[48, 3], [45, 100]],
        }
    }
    y, n = parse_orderbook_yes_no_best_ask_cents(ob)
    assert y == 50
    assert n == 45


def test_place_order_market_includes_side_price_cents(monkeypatch):
    """Kalshi requires one of yes_price/no_price even for market orders; gate needs min prob."""
    bodies: list = []

    def fake_request(self, method, path, *, body=None, **kwargs):
        if body is not None:
            bodies.append(body)
        return {"order": {"order_id": "m1", "status": "executed"}, "filled_count": 1, "filled_price": 5000}

    monkeypatch.setenv("KALSHI_MIN_ORDER_PROB", "0.5")
    monkeypatch.setattr(KalshiClient, "_request", fake_request)
    c = KalshiClient(api_key="test-bearer-token-not-pem")
    c.place_order(ticker="KX-T", side="yes", count=2)
    assert bodies[-1]["type"] == "market"
    assert bodies[-1].get("yes_price") in range(1, 100)
