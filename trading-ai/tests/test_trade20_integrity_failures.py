from trading_ai.validation.trade20_validation import load_trade20_report, maybe_process_trade20_closed_trade


def _t(i: int, *, proven: bool | None, supa: bool | None, tg_sent: bool | None) -> dict:
    trade = {
        "trade_id": f"if{i}",
        "venue_id": "coinbase",
        "gate_id": "gate_a",
        "symbol": "ETH-USD",
        "timestamp_close": f"2026-04-21T04:{i:02d}:00+00:00",
        "gross_pnl": 1.0,
        "fees_paid": 0.1,
        "net_pnl": 0.9,
        "exit_reason": "target",
    }
    if proven is not None:
        trade["final_execution_proven"] = proven
    if supa is not None:
        trade["supabase_synced"] = supa
    return trade, ({"telegram": {"sent": tg_sent}} if tg_sent is not None else {})


def test_trade20_integrity_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    # Force execution proof integrity to FAIL: only 15/20 proven true => 0.75 (<0.8) => FAIL.
    for i in range(1, 16):
        t, out = _t(i, proven=True, supa=True, tg_sent=True)
        maybe_process_trade20_closed_trade(t, post_trade_out=out)
    for i in range(16, 21):
        t, out = _t(i, proven=False, supa=False, tg_sent=False)
        maybe_process_trade20_closed_trade(t, post_trade_out=out)

    rep = load_trade20_report()
    infra = rep["infra_integrity"]
    j = rep["judgment"]

    assert rep["window_complete"] is True
    assert infra["execution_proof_integrity"]["status"] == "FAIL"
    assert j["infra_result"] == "broken"
    assert j["overall_result"] == "PAUSE_AND_FIX"

