"""Governance must run before NTE strategy live-routing approval."""

from __future__ import annotations

import pytest

from trading_ai.nte.execution import coinbase_engine as ce


def test_nte_entry_gates_governance_before_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def _gov(**kwargs: object) -> tuple:
        order.append("governance")
        return True, "ok", {}

    def _live(sid: str) -> bool:
        order.append("strategy")
        return True

    monkeypatch.setattr(ce, "check_new_order_allowed_full", _gov)
    monkeypatch.setattr(ce, "live_routing_permitted", _live)

    ok, kind, detail = ce._nte_entry_gates_coinbase(
        product_id="BTC-USD",
        strategy_route_label="mean_reversion",
        route_bucket="range|picked",
    )
    assert ok is True
    assert kind is None
    assert order == ["governance", "strategy"]


def test_governance_denied_skips_strategy_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy_called = False

    def _gov(**kwargs: object) -> tuple:
        return False, "joint_stale", {}

    def _live(sid: str) -> bool:
        nonlocal strategy_called
        strategy_called = True
        return True

    monkeypatch.setattr(ce, "check_new_order_allowed_full", _gov)
    monkeypatch.setattr(ce, "live_routing_permitted", _live)

    ok, kind, detail = ce._nte_entry_gates_coinbase(
        product_id="ETH-USD",
        strategy_route_label="continuation_pullback",
        route_bucket="trend|picked",
    )
    assert ok is False
    assert kind == "governance"
    assert detail == "joint_stale"
    assert strategy_called is False


def test_changing_strategy_label_does_not_swap_gate_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def _gov(**kwargs: object) -> tuple:
        order.append("governance")
        return True, "ok", {}

    def _live(sid: str) -> bool:
        order.append(f"strategy:{sid}")
        return True

    monkeypatch.setattr(ce, "check_new_order_allowed_full", _gov)
    monkeypatch.setattr(ce, "live_routing_permitted", _live)

    for label in ("A", "B", "micro_momentum_shadow"):
        order.clear()
        ok, _, _ = ce._nte_entry_gates_coinbase(
            product_id="BTC-USD",
            strategy_route_label=label,
            route_bucket="x",
        )
        assert ok
        assert order[0] == "governance"
        assert order[1].startswith("strategy:")
