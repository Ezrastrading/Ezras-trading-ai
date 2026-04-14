from trading_ai.automation.close_trade_reconciliation import reconcile_closed_trade_execution


def test_reconcile_close_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "c1",
        "result": "win",
        "roi_percent": 5.0,
        "capital_allocated": 100.0,
    }
    r = reconcile_closed_trade_execution(t)
    assert r.get("ok") is True
