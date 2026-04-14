from trading_ai.reporting.daily_decision_memo import generate_daily_memo


def test_generate_memo(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    r = generate_daily_memo(date_utc="2026-04-12")
    assert r["ok"] is True
    body = (tmp_path / "logs" / "daily_decision_memo.md").read_text(encoding="utf-8")
    for label in (
        "## 1. Activity summary",
        "## 3. Hard lockouts",
        "## 5. Venue truth sync",
        "## 9. Parameter governance",
        "## 10. Deterministic next actions",
    ):
        assert label in body
