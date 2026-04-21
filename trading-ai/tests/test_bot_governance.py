"""Bot governance layer — registry, factory, permissions, CEO hook (no live execution)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_factory import MIN_TRADES_FOR_SPECIALIZATION, create_bot_if_needed
from trading_ai.global_layer.bot_memory import append_lesson, ensure_bot_memory_files, read_performance
from trading_ai.global_layer.bot_permissions import action_allowed
from trading_ai.global_layer.bot_registry import (
    get_bots_by_avenue,
    get_bots_by_gate,
    get_bots_by_role,
    load_registry,
    register_bot,
    update_bot_performance,
)
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.budget_governor import load_budget_state, should_run_learning_after_trade
from trading_ai.global_layer.learning_distillation import approve_shared_lesson, propose_local_lesson, propose_shared_lesson
from trading_ai.global_layer.shared_truth import build_shared_truth


def _minimal_bot(bid: str, avenue: str = "A", gate: str = "gate_a", role: str = BotRole.SCANNER.value) -> dict:
    return {
        "bot_id": bid,
        "role": role,
        "avenue": avenue,
        "gate": gate,
        "version": "v1",
        "lifecycle_state": BotLifecycleState.SHADOW.value,
        "performance": {},
    }


def test_registry_crud_and_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    b1 = _minimal_bot("bot_scan_a", role=BotRole.SCANNER.value)
    register_bot(b1, path=regp)
    b2 = _minimal_bot("bot_dec_a", role=BotRole.DECISION.value)
    register_bot(b2, path=regp)
    assert len(get_bots_by_avenue("A", path=regp)) == 2
    assert len(get_bots_by_role(BotRole.SCANNER.value, path=regp)) == 1
    assert len(get_bots_by_gate("gate_a", path=regp)) == 2
    update_bot_performance("bot_scan_a", {"tasks_completed": 3}, path=regp)
    reg = load_registry(regp)
    assert any(str(x.get("bot_id")) == "bot_scan_a" and x.get("performance", {}).get("tasks_completed") == 3 for x in reg["bots"])


def test_duplicate_bot_id_and_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    register_bot(_minimal_bot("x", role=BotRole.SCANNER.value), path=regp)
    with pytest.raises(ValueError, match="duplicate_bot_id"):
        register_bot(_minimal_bot("x", role=BotRole.SCANNER.value), path=regp)
    register_bot(_minimal_bot("y", role=BotRole.DECISION.value), path=regp)
    with pytest.raises(ValueError, match="duplicate_scope_guard"):
        register_bot(_minimal_bot("z", role=BotRole.SCANNER.value), path=regp)


def test_factory_respects_containment_and_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "br.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    monkeypatch.setenv("EZRAS_BOT_SPAWN_COOLDOWN_SEC", "0")
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_spawn_manager.last_spawn_ts_path",
        lambda: tmp_path / "last_spawn_ts.json",
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.bot_factory.load_kill_switch",
        lambda: {"orchestration_frozen": False},
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.bot_factory.load_containment",
        lambda: {"freeze_all_new_bots": True, "avenue_containment": {}, "gate_containment": {}},
    )
    out = create_bot_if_needed(
        {
            "avenue": "A",
            "gate": "gate_a",
            "role": BotRole.LEARNING.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": MIN_TRADES_FOR_SPECIALIZATION,
            "measured_gap": True,
        },
        registry_path=regp,
    )
    assert out["created"] is False
    monkeypatch.setattr(
        "trading_ai.global_layer.bot_factory.load_containment",
        lambda: {"freeze_all_new_bots": False, "avenue_containment": {}, "gate_containment": {}},
    )
    out2 = create_bot_if_needed(
        {
            "avenue": "A",
            "gate": "gate_a",
            "role": BotRole.LEARNING.value,
            "version": "v1",
            "performance_threshold_failed": True,
            "trade_count": MIN_TRADES_FOR_SPECIALIZATION,
            "measured_gap": True,
        },
        registry_path=regp,
    )
    assert out2.get("created") is True


def test_memory_files_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.governance import storage_architecture

    monkeypatch.setattr(storage_architecture, "shark_data_dir", lambda: tmp_path / "shark")
    paths = ensure_bot_memory_files("bot_mem_1")
    for k in ("performance", "trades", "lessons"):
        assert paths[k].is_file()
    propose_local_lesson("bot_mem_1", {"text": "lesson"})
    perf = read_performance("bot_mem_1")
    assert "metrics" in perf


def test_permissions_scanner_cannot_execute() -> None:
    b = _minimal_bot("s", role=BotRole.SCANNER.value)
    ok, why = action_allowed(b, "submit_intent_through_pipeline")
    assert ok is False


def test_shared_truth_reads_runtime(tmp_path: Path) -> None:
    (tmp_path / "data/control").mkdir(parents=True)
    (tmp_path / "execution_proof").mkdir(parents=True)
    (tmp_path / "execution_proof/live_execution_validation.json").write_text(
        json.dumps({"execution_success": True, "FINAL_EXECUTION_PROVEN": True, "runtime_root": str(tmp_path)}),
        encoding="utf-8",
    )
    st = build_shared_truth(runtime_root=tmp_path)
    assert st["runtime_root"] == str(tmp_path.resolve())


def test_learning_distillation_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)

    def _gov() -> Path:
        return tmp_path

    monkeypatch.setattr("trading_ai.global_layer.learning_distillation.global_layer_governance_dir", _gov)
    propose_shared_lesson("b1", {"text": "x"})
    pend = json.loads((tmp_path / "shared_learning_pending.json").read_text(encoding="utf-8"))
    assert len(pend["items"]) == 1
    ok, _ = approve_shared_lesson(0, "CEO")
    assert ok is True
    shared = json.loads((tmp_path / "shared_approved_learning.json").read_text(encoding="utf-8"))
    assert len(shared.get("lessons") or []) == 1


def test_ceo_review_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "reg.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    monkeypatch.setattr("trading_ai.global_layer.audit_trail.audit_log_path", lambda: tmp_path / "audit.jsonl")
    monkeypatch.setattr("trading_ai.global_layer.budget_governor.budget_state_path", lambda: tmp_path / "bud.json")
    register_bot(_minimal_bot("b1"), path=regp)
    from trading_ai.global_layer.bot_ceo_review import review_all_bots

    summ = review_all_bots(registry_path=regp)
    assert summ["bot_count"] == 1
    assert summ["reviews"]


def test_trade_learning_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trading_ai.global_layer.budget_governor.budget_state_path",
        lambda: tmp_path / "budget.json",
    )
    st = load_budget_state()
    st["trades_since_learning"] = 18
    from trading_ai.global_layer import budget_governor as bg

    bg.save_budget_state(st)
    ok, _ = should_run_learning_after_trade()
    assert ok is False
    ok2, _ = should_run_learning_after_trade()
    assert ok2 is True
