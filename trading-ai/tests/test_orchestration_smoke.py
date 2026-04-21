"""End-to-end smoke: orchestration registry, spawn, authority, permissions, CEO review, conflicts, heartbeat."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_factory import create_bot_if_needed
from trading_ai.global_layer.bot_registry import get_bot, load_registry, patch_bot
from trading_ai.global_layer.bot_types import BotRole
from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review
from trading_ai.global_layer.execution_authority import (
    assert_single_authority_invariant,
    grant_execution_authority,
    load_authority_registry,
    revoke_execution_authority,
)
from trading_ai.global_layer.orchestration_conflicts import log_conflict, load_recent_conflicts
from trading_ai.global_layer.orchestration_heartbeat import run_stale_sweep, touch_heartbeat
from trading_ai.global_layer.orchestration_kill_switch import freeze_orchestration
from trading_ai.global_layer.orchestration_permissions import bot_may_place_live_orders
from trading_ai.global_layer.orchestration_schema import PermissionLevel
from trading_ai.global_layer.orchestration_task_assignment import try_claim_task, release_task
from trading_ai.global_layer.promotion_contracts import evaluate_promotion_contract
from trading_ai.global_layer.orchestration_paths import ceo_daily_review_path, orchestration_root


@pytest.fixture
def orch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setenv("EZRAS_BOT_SPAWN_COOLDOWN_SEC", "0")
    gdir = tmp_path / "gov"
    gdir.mkdir(parents=True, exist_ok=True)

    def _gov() -> Path:
        return gdir

    monkeypatch.setattr("trading_ai.global_layer._bot_paths.global_layer_governance_dir", _gov)
    monkeypatch.setattr("trading_ai.global_layer.orchestration_paths.global_layer_governance_dir", _gov)
    monkeypatch.setattr("trading_ai.global_layer.budget_governor.budget_state_path", lambda: gdir / "budget.json")
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: gdir / "orchestration_kill_switch.json",
    )
    return tmp_path


def test_smoke_full_chain(orch_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])

    # 1 registry init
    reg = load_registry(regp)
    assert reg["truth_version"] == "bot_registry_v2"

    # 2 spawn manager creates bot with observe_only default
    out = create_bot_if_needed(
        {
            "avenue": "A",
            "gate": "gate_a",
            "role": BotRole.SCANNER.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": 25,
            "measured_gap": True,
            "spawn_reason": "smoke_test",
        },
        registry_path=regp,
    )
    assert out.get("created") is True
    bid = str(out["bot_id"])
    b = get_bot(bid, path=regp)
    assert b is not None
    assert str(b.get("permission_level")) == PermissionLevel.SHADOW_EXECUTION.value

    # 3 duplicate guard — same scope second spawn should fail (duplicate in registry vs new normalized)
    out2 = create_bot_if_needed(
        {
            "avenue": "A",
            "gate": "gate_a",
            "role": BotRole.SCANNER.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": 25,
            "measured_gap": True,
        },
        registry_path=regp,
    )
    assert out2.get("created") is False

    # 4 heartbeat
    touch_heartbeat(bid, path=regp)
    b2 = get_bot(bid, path=regp)
    assert b2.get("last_heartbeat_at")

    # 5 CEO daily review artifact
    daily = write_daily_ceo_review(registry_path=regp)
    assert daily.get("bot_total") == 1
    assert ceo_daily_review_path().is_file()

    # 6 cost governor state file exists when touched elsewhere — budget load ok
    from trading_ai.global_layer.budget_governor import load_budget_state

    assert "global_daily_token_budget" in load_budget_state()

    # 7 conflict log
    log_conflict(
        conflict_type="analysis",
        bot_a=bid,
        bot_b="other",
        avenue="A",
        gate="gate_a",
        evidence_a={"x": 1},
        evidence_b={"x": 2},
        resolution="defer_to_canonical_execution",
    )
    assert len(load_recent_conflicts(5)) >= 1

    # 8 promotion contract blocks unqualified (no heartbeat in minimal bot dict for evaluate)
    bot_min = {"performance": {}, "demotion_risk": False}
    ok_eval, _ = evaluate_promotion_contract(bot_min, {"contract_id": "x"})
    assert ok_eval is False

    # 9 execution authority uniqueness
    ok_inv, _ = assert_single_authority_invariant()
    assert ok_inv is True
    grant_execution_authority(
        bot_id=bid,
        avenue="A",
        gate="gate_a",
        route="default",
        contract_ref="smoke_contract",
        approved_by="CEO",
    )
    assert load_authority_registry()["slots"]
    revoke_execution_authority("A", "gate_a", "default", reason="smoke_cleanup")

    # 10 shadow cannot place live orders without full slot + permission
    assert bot_may_place_live_orders(b2)[0] is False

    # 11 promoted path still blocked without execution_authority permission_level on bot
    patch_bot(
        bid,
        {"permission_level": PermissionLevel.PROMOTED_EXECUTION.value},
        path=regp,
    )
    b3 = get_bot(bid, path=regp)
    assert bot_may_place_live_orders(b3)[0] is False

    # 12 task duplicate guard
    ok_claim, _ = try_claim_task(
        bot_id=bid,
        avenue="A",
        gate="gate_a",
        route="default",
        bot_class=str(b3.get("bot_class")),
        task_type="scan",
        task_id="t1",
    )
    assert ok_claim is True
    release_task(avenue="A", gate="gate_a", route="default", bot_class=str(b3.get("bot_class")), task_type="scan", task_id="t1")

    # 13 kill switch blocks new spawn
    freeze_orchestration(True)
    out3 = create_bot_if_needed(
        {
            "avenue": "B",
            "gate": "gate_b",
            "role": BotRole.LEARNING.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": 25,
            "measured_gap": True,
        },
        registry_path=regp,
    )
    assert out3.get("created") is False
    freeze_orchestration(False)

    # 14 stale sweep — high threshold leaves recently touched bot active
    run_stale_sweep(stale_after_sec=86400, path=regp)
    b4 = get_bot(bid, path=regp)
    assert b4.get("status") == "active"

    assert orchestration_root().exists()
