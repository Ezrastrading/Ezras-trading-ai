from trading_ai.risk import hard_lockouts as hl


def test_can_open_when_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    co = hl.can_open_new_trade()
    assert co["allowed"] is True


def test_simulate_daily_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = hl.simulate_daily_loss(5.0)
    assert st.get("daily_lockout_active") is True
    hl.clear_daily_override()
    st2 = hl.get_effective_lockout()
    assert st2.get("daily_lockout_active") is False


def test_weekly_manual_override_recorded(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    hl.simulate_weekly_drawdown(9.0)
    assert hl.get_effective_lockout().get("weekly_lockout_active") is True
    out = hl.clear_weekly_lockout_manual(actor="tester", reason="ops approved")
    assert out.get("ok") is True
    assert out.get("weekly_lockout_active") is False
    raw = json.loads((tmp_path / "state" / "hard_lockout_state.json").read_text(encoding="utf-8"))
    assert any((h.get("kind") == "weekly_lockout_clear_manual") for h in (raw.get("override_history") or []))
