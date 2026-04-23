from __future__ import annotations


def test_take_profit_net_pnl_gate_blocks_when_net_negative(monkeypatch) -> None:
    from trading_ai.live_micro.position_manager import _estimate_net_pnl_usd

    monkeypatch.setenv("EZRA_LIVE_MICRO_EST_TOTAL_FEES_PCT", "0.01")
    monkeypatch.setenv("EZRA_LIVE_MICRO_EXIT_SLIPPAGE_BPS", "10")
    # Buy $10 worth; exit mid implies tiny gain but fees wipe it out.
    pnl = _estimate_net_pnl_usd(mid=100.05, base_qty=0.1, quote_spent=10.0)
    assert abs(float(pnl["gross_proceeds_est"]) - 10.005) < 1e-9
    assert pnl["net_pnl_est"] < 0


def test_take_profit_net_pnl_gate_passes_when_net_positive(monkeypatch) -> None:
    from trading_ai.live_micro.position_manager import _estimate_net_pnl_usd

    monkeypatch.setenv("EZRA_LIVE_MICRO_EST_TOTAL_FEES_PCT", "0.001")
    monkeypatch.setenv("EZRA_LIVE_MICRO_EXIT_SLIPPAGE_BPS", "0")
    pnl = _estimate_net_pnl_usd(mid=105.0, base_qty=0.1, quote_spent=10.0)
    assert pnl["net_pnl_est"] > 0

