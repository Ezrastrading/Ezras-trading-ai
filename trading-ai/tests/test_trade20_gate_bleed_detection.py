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


def _t(i: int, *, gate: str, net: float) -> dict:
    gross = max(0.1, net + 0.5)  # keep gross positive; fees small
    fees = max(0.0, gross - net)
    return {
        **_STRICT,
        "trade_id": f"gb{i}_{gate}",
        "venue_id": "coinbase",
        "gate_id": gate,
        "symbol": "BTC-USD",
        "timestamp_open": f"2026-04-21T01:{i:02d}:00+00:00",
        "timestamp_close": f"2026-04-21T02:{i:02d}:00+00:00",
        "gross_pnl": gross,
        "fees_paid": fees,
        "net_pnl": net,
        "exit_reason": "target",
        "final_execution_proven": True,
    }


def test_trade20_gate_bleed_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # gate_good: +2 each, 10 trades => +20
    # gate_bad:  -4 each, 10 trades => -40 (profit factor < 1, net < 0 => bleeding)
    i = 1
    for _ in range(10):
        maybe_process_trade20_closed_trade(_t(i, gate="gate_good", net=2.0))
        i += 1
    for _ in range(10):
        maybe_process_trade20_closed_trade(_t(i, gate="gate_bad", net=-4.0))
        i += 1

    rep = load_trade20_report()
    j = rep["judgment"]

    assert rep["window_complete"] is True
    assert rep["by_gate"]["gate_bad"]["status"] == "bleeding"
    assert rep["by_gate"]["gate_good"]["status"] in ("healthy", "mixed")
    assert j["gate_result"] == "one_gate_bleeding"

