from __future__ import annotations

import time
from pathlib import Path

import pytest


def test_corrupt_unrepairable_fill_does_not_persist_open_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    If fills exist but lack fundamentals (e.g., missing price), live_micro must NOT persist an "open"
    position with bogus/None base_qty; it must remain pending_entry and log invalid_position_base_qty.
    """
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Reuse existing contract green helper from sibling test module.
    from test_live_micro_candidate_execution import _seed_micro_contract_green

    _seed_micro_contract_green(tmp_path, monkeypatch)

    from trading_ai.global_layer.review_storage import ReviewStorage

    st = ReviewStorage()
    st.ensure_review_files()
    cq = st.load_json("candidate_queue.json")
    cq["items"] = [
        {"id": "c1", "ts": time.time(), "avenue_id": "B", "gate_id": "gate_b", "product_id": "BTC-USD", "status": "new"}
    ]
    st.save_json("candidate_queue.json", cq)

    # Force required quote truth available.
    monkeypatch.setattr(
        "trading_ai.live_micro.quote_balance_truth.required_quote_available",
        lambda *_, **__: (True, 50.0, {"balances": {"USD": 50.0}}),
    )
    # Force min notional low so sizing passes.
    monkeypatch.setattr(
        "trading_ai.nte.execution.coinbase_min_notional.resolve_coinbase_min_notional_usd",
        lambda *_, **__: (1.0, "test", {"product_id": "BTC-USD"}),
    )
    monkeypatch.setattr(
        "trading_ai.control.system_execution_lock.require_live_execution_allowed",
        lambda *_, **__: (True, "ok"),
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.gap_engine.evaluate_candidate",
        lambda _c: type("D", (), {"should_trade": True, "rejection_reasons": []})(),
    )
    monkeypatch.setattr(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        lambda _p: {"best_bid": "100", "best_ask": "101", "price": "100.5", "time": time.time()},
    )

    class _BadFillClient:
        def place_market_buy(self, *_a, **_k):
            return type("R", (), {"success": True, "status": "placed", "reason": None, "order_id": "ord_bad"})()

        def get_fills(self, _oid: str):
            # size_in_quote true but missing price => unreparable fundamentals
            return [{"size_in_quote": True, "size": "9.00", "commission": "0.01"}]

    monkeypatch.setattr("trading_ai.shark.outlets.coinbase.CoinbaseClient", _BadFillClient)

    from trading_ai.live_micro.candidate_execution import run_live_micro_candidate_execution_once
    from trading_ai.live_micro.positions import load_open_positions

    out = run_live_micro_candidate_execution_once(runtime_root=tmp_path)
    assert out.get("ok") is True
    # Should not create an open position
    pos = load_open_positions(tmp_path)
    row = [p for p in pos if str(p.get("position_id")) == "ord_bad"][0]
    assert row.get("status") == "pending_entry"
    ev = (tmp_path / "data" / "control" / "live_micro_position_journal.jsonl").read_text(encoding="utf-8")
    assert "invalid_position_base_qty" in ev

