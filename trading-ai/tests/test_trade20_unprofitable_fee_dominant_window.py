from trading_ai.validation.trade20_validation import load_trade20_report, maybe_process_trade20_closed_trade


def _fee_flip_trade(i: int) -> dict:
    # gross>0 but net<0 due to fees: explicit fee flip
    return {
        "trade_id": f"ff{i}",
        "venue_id": "coinbase",
        "gate_id": "gate_a",
        "symbol": "ETH-USD",
        "timestamp_open": f"2026-04-21T01:{i:02d}:00+00:00",
        "timestamp_close": f"2026-04-21T01:{i:02d}:20+00:00",
        "gross_pnl": 1.00,
        "fees_paid": 2.00,
        "net_pnl": -1.00,
        "exit_reason": "target",
        "final_execution_proven": True,
    }


def test_trade20_unprofitable_fee_dominant_window(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    for i in range(1, 21):
        out = maybe_process_trade20_closed_trade(_fee_flip_trade(i), post_trade_out={"telegram": {"sent": True}})
        assert out["status"] == "accepted"

    rep = load_trade20_report()
    j = rep["judgment"]

    assert rep["window_complete"] is True
    assert rep["global_metrics"]["profitability_metrics"]["net_pnl_total"] < 0
    assert rep["failure_patterns"]["fee_dominance_cluster"]["active"] is True
    assert j["overall_result"] == "PAUSE_AND_FIX"

