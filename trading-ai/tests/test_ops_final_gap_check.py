from trading_ai.ops.final_gap_check import run_final_gap_check


def test_gap_check_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = run_final_gap_check()
    assert "ok" in out
    assert "status" in out
    assert "failures" in out
    assert "warnings" in out
    assert "remaining_limitations" in out
    assert "checked_components" in out
    assert out["remaining_limitations"] == []
