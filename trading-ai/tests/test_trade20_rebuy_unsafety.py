from trading_ai.validation.trade20_validation import load_trade20_report, maybe_process_trade20_closed_trade

_STRICT = {
    "gap_type": "probability_gap",
    "edge_percent": 1.0,
    "confidence_score": 0.85,
    "liquidity_score": 0.9,
    "execution_grade": "A",
    "entry_slippage_bps": 1.0,
    "exit_slippage_bps": 1.0,
}


def _t(i: int, *, net: float, rebuy_allowed: bool) -> dict:
    gross = 1.0
    fees = 0.1
    return {
        **_STRICT,
        "trade_id": f"rb{i}",
        "venue_id": "coinbase",
        "gate_id": "gate_a",
        "symbol": "ETH-USD",
        "timestamp_open": f"2026-04-21T02:{i:02d}:00+00:00",
        "timestamp_close": f"2026-04-21T03:{i:02d}:00+00:00",
        "gross_pnl": gross,
        "fees_paid": fees,
        "net_pnl": net,
        "exit_reason": "stop_loss" if net < 0 else "target",
        "final_execution_proven": True,
        "rebuy_handoff_attempted": True,
        "rebuy_handoff_allowed": rebuy_allowed,
    }


def test_trade20_rebuy_unsafety(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # Make multiple losing trades where rebuy is still allowed (unsafe cluster trigger).
    for i in range(1, 11):
        maybe_process_trade20_closed_trade(_t(i, net=-1.0, rebuy_allowed=True))
    for i in range(11, 21):
        maybe_process_trade20_closed_trade(_t(i, net=1.0, rebuy_allowed=True))

    rep = load_trade20_report()
    j = rep["judgment"]

    assert rep["window_complete"] is True
    assert rep["failure_patterns"]["rebuy_quality_cluster"]["active"] is True
    assert j["rebuy_result"] == "unsafe"
    assert j["overall_result"] != "READY_LIVE"

