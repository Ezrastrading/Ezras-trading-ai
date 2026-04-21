from __future__ import annotations

from pathlib import Path

from trading_ai.nte.execution.profit_enforcement import (
    ProfitEnforcementConfig,
    evaluate_profit_enforcement,
)


def test_profit_enforcement_blocks_nonpositive_net_edge(tmp_path: Path) -> None:
    d = evaluate_profit_enforcement(
        runtime_root=tmp_path,
        trade_id="t1",
        avenue_id="A",
        gate_id="gate_a",
        product_id="BTC-USD",
        quote_usd=10.0,
        spread_bps=25.0,
        fee_bps_round_trip=20.0,
        expected_gross_move_bps=10.0,  # too small
        expected_risk_bps=10.0,
        config=ProfitEnforcementConfig(min_expected_net_edge_bps=2.0, min_expected_net_pnl_usd=0.01, min_reward_to_risk=1.0),
        write_artifact=True,
    )
    assert d["allowed"] is False
    assert any(str(x).startswith("blocked_") for x in d.get("reason_codes") or [])
    assert (tmp_path / "data" / "control" / "profit_enforcement_truth.json").is_file()


def test_profit_enforcement_allows_positive_net_edge(tmp_path: Path) -> None:
    d = evaluate_profit_enforcement(
        runtime_root=tmp_path,
        trade_id="t2",
        avenue_id="A",
        gate_id="gate_b",
        product_id="LINK-USD",
        quote_usd=50.0,
        spread_bps=5.0,
        fee_bps_round_trip=10.0,
        expected_gross_move_bps=40.0,
        expected_risk_bps=25.0,
        config=ProfitEnforcementConfig(min_expected_net_edge_bps=2.0, min_expected_net_pnl_usd=0.05, min_reward_to_risk=1.05),
        write_artifact=True,
    )
    assert d["allowed"] is True
    assert d["reason_codes"] == ["ok"]

