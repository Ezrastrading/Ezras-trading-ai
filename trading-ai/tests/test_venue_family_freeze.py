"""Venue-family orchestration freeze — scoped avenues, unrelated avenue unfrozen."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.orchestration_kill_switch import (
    freeze_orchestration,
    freeze_venue_family,
    load_kill_switch,
    orchestration_blocked_for_bot,
    save_kill_switch,
)
def test_venue_family_freeze_targets_family_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "orchestration_kill_switch.json"
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: p,
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    save_kill_switch(
        {
            "truth_version": "orchestration_kill_switch_v1",
            "orchestration_frozen": False,
            "avenue": {},
            "venue_family": {},
            "gate": {},
            "bot_class": {},
            "bot_id": {},
        }
    )
    out = freeze_venue_family("spot_crypto", True)
    assert "coinbase" in (out.get("freeze_target_avenue_ids") or [])
    blocked_cb, _ = orchestration_blocked_for_bot({"avenue": "coinbase", "gate": "gate_a", "bot_class": "x", "bot_id": "b1"})
    assert blocked_cb is True
    blocked_k, why = orchestration_blocked_for_bot({"avenue": "kalshi", "gate": "gate_b", "bot_class": "x", "bot_id": "b2"})
    assert blocked_k is False, why


def test_global_freeze_still_blocks_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "orchestration_kill_switch.json"
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: p,
    )
    freeze_orchestration(True)
    c = load_kill_switch()
    assert c.get("orchestration_frozen") is True


def test_activate_halt_venue_family_truth_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_KILL_SWITCH_FREEZE_ORCHESTRATION", "1")
    orch = tmp_path / "orchestration_kill_switch.json"
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: orch,
    )
    from trading_ai.safety.kill_switch_engine import activate_halt

    out = activate_halt(
        "MANUAL_OPERATOR_HALT",
        source_component="test",
        severity="CRITICAL",
        immediate_action_required="review",
        orchestration_freeze_scope="venue_family",
        venue_family_id="spot_crypto",
        runtime_root=tmp_path,
        broadcast_system_guard=False,
        rehearsal_mode=False,
    )
    d = (out.get("truth") or {}).get("detail") or {}
    assert d.get("resolved_freeze_scope") == "venue_family"
    assert "coinbase" in (d.get("freeze_target_ids") or [])
