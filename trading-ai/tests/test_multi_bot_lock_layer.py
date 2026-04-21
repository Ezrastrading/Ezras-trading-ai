"""Lock layer — constitution, rungs, truth writers, operator snapshot, scheduling."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_registry import get_bot, register_bot
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.deterministic_autonomous_orchestration import run_auto_promotion_cycle
from trading_ai.global_layer.lock_layer import (
    TruthDomain,
    assert_no_rung_skip,
    build_operator_snapshot,
    compute_bot_quality_contract,
    execution_rung_for_promotion_tier,
    finalize_promotion_cycle_truth,
    is_canonical_writer,
    record_incident,
    schedule_bots_fairness,
    validate_handoff_envelope,
)
from trading_ai.global_layer.lock_layer.constitution import OBJECTIVE_HIERARCHY, SYSTEM_CONSTITUTION
from trading_ai.global_layer.lock_layer.promotion_rung import ExecutionRung
from trading_ai.global_layer.lock_layer.truth_writers import CANONICAL_WRITER_IDS
from trading_ai.global_layer.orchestration_paths import bot_auto_promotion_truth_path, operator_snapshot_path
from trading_ai.global_layer.orchestration_schema import PromotionTier


@pytest.fixture
def lock_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(tmp_path / "registry.json"))
    gdir = tmp_path / "gov"
    gdir.mkdir(parents=True, exist_ok=True)

    def _gov() -> Path:
        return gdir

    monkeypatch.setattr("trading_ai.global_layer._bot_paths.global_layer_governance_dir", _gov)
    monkeypatch.setattr("trading_ai.global_layer.orchestration_paths.global_layer_governance_dir", _gov)
    return tmp_path


def test_constitution_and_objective_order():
    assert OBJECTIVE_HIERARCHY[0] == "capital_protection"
    assert "forbidden" in SYSTEM_CONSTITUTION


def test_execution_rung_maps_tiers():
    assert execution_rung_for_promotion_tier("T0") == ExecutionRung.SHADOW
    assert execution_rung_for_promotion_tier("T1") == ExecutionRung.PAPER
    assert execution_rung_for_promotion_tier("T4") == ExecutionRung.SCALED_LIVE


def test_no_skip_tier_jump_enforced():
    ok, why = assert_no_rung_skip("T0", "T2")
    assert ok is False
    assert "skip_forbidden" in why
    ok2, why2 = assert_no_rung_skip("T0", "T1")
    assert ok2 is True


def test_truth_writer_rejects_bad_id():
    with pytest.raises(PermissionError):
        finalize_promotion_cycle_truth({}, writer_id="random_bot")


def test_truth_writer_accepts_canonical():
    finalize_promotion_cycle_truth({"x": 1}, writer_id=CANONICAL_WRITER_IDS[TruthDomain.PROMOTION])
    assert is_canonical_writer(TruthDomain.PROMOTION, CANONICAL_WRITER_IDS[TruthDomain.PROMOTION])


def test_handoff_contract():
    ok, errs = validate_handoff_envelope(
        {
            "handoff_id": "h1",
            "from_bot_id": "a",
            "to_bot_id": "b",
            "input_ref": "ref",
            "output_schema": "schema",
            "timeout_sec": 30,
            "retry_policy": {"max_attempts": 2},
            "rejection_rule": "reject_if_invalid",
        }
    )
    assert ok is True and not errs


def test_operator_snapshot_writes(lock_env: Path):
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "snap1",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "last_heartbeat_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "performance": {"composite": {"utility_score": 0.8}},
        },
        path=regp,
    )
    out = build_operator_snapshot(registry_path=regp)
    assert out["counts"]["total_bots"] == 1
    assert operator_snapshot_path().is_file()


def test_scheduler_and_quality(lock_env: Path):
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "q1",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "promotion_scorecard": {
                "expectancy": 0.1,
                "avg_slippage_bps": 5.0,
                "truth_conflict_unresolved": 0,
                "duplicate_task_violations": 0,
            },
            "performance": {"token_cost": 100.0, "composite": {"utility_score": 0.6}},
        },
        path=regp,
    )
    b = get_bot("q1", path=regp)
    qc = compute_bot_quality_contract(b or {})
    assert 0.0 <= float(qc["composite_quality"]) <= 1.0
    ranked = schedule_bots_fairness([dict(b or {})])
    assert ranked[0].get("bot_id") == "q1"


def test_incident_record(lock_env: Path):
    r = record_incident(
        "api_failure",
        domain="execution",
        caused_by_bot_id="b1",
        severity="high",
        action_taken="freeze_lane",
        freeze_scope="gate",
        details={"gate": "gate_a"},
    )
    assert r.get("postmortem_required") is True


def test_smoke_promotion_writes_wrapped_truth(lock_env: Path):
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "smoke_bot",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "last_heartbeat_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "promotion_scorecard": {
                "shadow_trade_count": 120,
                "evaluation_count": 60,
                "sample_diversity_score": 0.5,
                "expectancy": 0.1,
                "profit_factor": 1.2,
                "max_drawdown_pct": 5.0,
                "avg_slippage_bps": 10.0,
                "avg_latency_ms": 100.0,
                "truth_conflict_unresolved": 0,
                "duplicate_task_violations": 0,
                "unauthorized_writes": 0,
                "promotion_readiness_score": 0.7,
                "clean_live_cycles": 25,
            },
            "governance_flags": {"ceo_review_pass": True, "risk_review_pass": True},
            "external_eval_signals": {
                "performance_evaluator_ok": True,
                "risk_engine_ok": True,
                "truth_layer_ok": True,
                "orchestration_policy_ok": True,
            },
        },
        path=regp,
    )
    run_auto_promotion_cycle(registry_path=regp)
    raw = json.loads(bot_auto_promotion_truth_path().read_text(encoding="utf-8"))
    assert raw.get("payload") is not None
    inner = raw["payload"]
    assert inner.get("truth_version") == "bot_auto_promotion_truth_v1"
    b = get_bot("smoke_bot", path=regp)
    assert str(b.get("promotion_tier")) == PromotionTier.T1.value
    assert str(b.get("execution_rung")) == ExecutionRung.PAPER.value
