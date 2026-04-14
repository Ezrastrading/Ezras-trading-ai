from trading_ai.analysis.trade_quality_score import score_closed_trade


def test_score_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "q1",
        "result": "loss",
        "roi_percent": -2.0,
        "signal_score": 8,
        "position_sizing_meta": {"effective_bucket": "NORMAL", "approval_status": "APPROVED"},
    }
    s = score_closed_trade(t, reconciliation={"execution_quality_verdict": "CLEAN"})
    assert "overall_quality_score" in s
    assert s["quality_verdict"] in ("HIGH", "MEDIUM", "LOW")
