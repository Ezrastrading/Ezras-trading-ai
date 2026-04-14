from trading_ai.governance import parameter_governance as pg


def test_record_and_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    r = pg.record_parameter_change(
        parameter_name="test_param",
        old_value="1",
        new_value="2",
        reason="unit test change",
    )
    assert r["ok"] is True
    recent = pg.get_recent_parameter_changes(limit=5)
    assert len(recent) >= 1


def test_drift_check_writes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    d1 = pg.check_tracked_parameter_drift(trigger="test")
    assert d1.get("note") == "initial_snapshot_written" or "current_fingerprint" in d1
    d2 = pg.check_tracked_parameter_drift(trigger="test2")
    assert d2.get("drift_detected") is False
