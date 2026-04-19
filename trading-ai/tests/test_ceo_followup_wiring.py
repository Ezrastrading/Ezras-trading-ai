"""CEO action follow-up package surfaces open actions + baselines."""

from __future__ import annotations

from trading_ai.nte.ceo.action_tracker import append_action
from trading_ai.nte.ceo.followup import prepare_ceo_followup_briefing


def test_prepare_followup_lists_open_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    append_action(
        session_id="s1",
        avenue_scope="coinbase",
        action_type="tune",
        description="tighten spread filter",
        reason="ceo",
        priority="high",
        owner_module="test",
    )
    fu = prepare_ceo_followup_briefing(session_id="s2")
    assert "open_actions" in fu
    assert len(fu["open_actions"]) >= 1
    assert "CEO action follow-up" in fu["markdown"]
