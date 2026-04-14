"""final-gap-check must FAIL when unresolved CRITICAL exceptions exist."""

from trading_ai.ops import final_gap_check as fgc


def test_gap_check_fails_on_critical(monkeypatch, tmp_path):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    def _crit():
        return [
            {
                "id": "x1",
                "severity": "CRITICAL",
                "category": "test",
                "message": "boom",
                "resolved": False,
            }
        ]

    monkeypatch.setattr("trading_ai.ops.exception_dashboard.list_open_exceptions", _crit)
    out = fgc.run_final_gap_check()
    assert out["ok"] is False
    assert out["status"] == "FAIL"
    assert any("critical" in x.lower() for x in out["failures"])
