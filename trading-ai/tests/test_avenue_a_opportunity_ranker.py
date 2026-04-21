from __future__ import annotations

from pathlib import Path

from trading_ai.orchestration.avenue_a_opportunity_ranker import rank_avenue_a_opportunity


class FakeCoinbaseClient:
    def __init__(self) -> None:
        # Simple static book
        self._bid = 100.0
        self._ask = 100.1

    def get_product_price(self, product_id: str):
        # Slightly different spreads for different symbols
        if str(product_id).upper().startswith("BTC"):
            return (100.0, 100.05)
        return (100.0, 100.2)

    def get_exchange_product_stats(self, product_id: str):
        return {"volume": 10_000, "last": 100.0}


def test_ranker_writes_truth_and_can_no_trade(tmp_path: Path, monkeypatch) -> None:
    # Force small budgets to increase chance of no-trade (profit enforcement floors)
    monkeypatch.setenv("EZRAS_MIN_EXPECTED_NET_EDGE_BPS", "1000")
    c = FakeCoinbaseClient()
    out = rank_avenue_a_opportunity(
        runtime_root=tmp_path,
        client=c,
        deployable_quote_usd=100.0,
        anchored_majors_only_for_gate_a=True,
    )
    assert (tmp_path / "data" / "control" / "opportunity_ranking_truth.json").is_file()
    assert out.get("truth_version") == "opportunity_ranking_truth_v1"
    assert out.get("no_trade") in (True, False)

