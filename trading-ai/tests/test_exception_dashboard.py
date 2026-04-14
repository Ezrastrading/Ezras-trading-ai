from trading_ai.ops.exception_dashboard import add_exception_event, dashboard_status, mark_resolved


def test_add_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    e = add_exception_event(category="missing_data", message="unit test", severity="LOW", requires_review=False)
    assert dashboard_status()["open_count"] >= 1
    mark_resolved(e["id"])
