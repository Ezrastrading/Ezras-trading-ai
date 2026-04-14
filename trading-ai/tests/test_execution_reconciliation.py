from trading_ai.execution.execution_reconciliation import (
    reconcile_execution_intent_vs_result,
    record_execution_submission,
    get_execution_reconciliation_status,
)


def test_reconcile_flags_size_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    r = reconcile_execution_intent_vs_result(
        trade_id="t1",
        requested_size=100,
        approved_size=50,
        submitted_size=40,
        filled_size=40,
        avg_fill_price=0.5,
        expected_entry_price=0.5,
        fees=0.1,
    )
    assert r["execution_quality_verdict"] in ("SIZE_DRIFT", "DISCREPANCY")
    assert r["requires_review"] is True


def test_record_submission_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    record_execution_submission(
        trade_id="t2", requested_size=10, approved_size=10, submitted_size=10, expected_entry_price=0.4
    )
    st = get_execution_reconciliation_status(trade_id="t2")
    assert st["trade"]["submitted_size"] == 10.0
