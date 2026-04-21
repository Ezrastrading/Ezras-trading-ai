from trading_ai.validation.trade20_validation import load_trade20_report, maybe_process_trade20_closed_trade


def _trade(i: int) -> dict:
    return {
        "trade_id": f"t{i}",
        "venue_id": "coinbase",
        "gate_id": "gate_a",
        "symbol": "ETH-USD",
        "timestamp_open": f"2026-04-21T00:{i:02d}:00+00:00",
        "timestamp_close": f"2026-04-21T00:{i:02d}:30+00:00",
        "hold_seconds": 30,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "gross_pnl": 10.0,
        "fees_paid": 1.0,
        "net_pnl": 9.0,
        "exit_reason": "target",
        "side": "BUY",
        "final_execution_proven": True,
        "supabase_synced": True,
        "rebuy_handoff_attempted": False,
        "rebuy_handoff_allowed": False,
    }


def test_trade20_profitable_clean_window(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    for i in range(1, 21):
        out = maybe_process_trade20_closed_trade(_trade(i), post_trade_out={"telegram": {"sent": True}})
        assert out["status"] == "accepted"

    rep = load_trade20_report()
    j = rep["judgment"]

    assert rep["window_complete"] is True
    assert (rep["global_metrics"]["profitability_metrics"]["net_pnl_total"]) > 0
    assert (rep["global_metrics"]["quality_metrics"]["profit_factor_net"]) > 1
    assert j["overall_result"] == "READY_LIVE"

