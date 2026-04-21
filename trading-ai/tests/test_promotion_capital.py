"""Deterministic promotion contract, capital governor, single authority, and auto cycles."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_registry import get_bot, register_bot
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.capital_governor import (
    check_live_quote_allowed,
    evaluate_capital_scale_up,
    load_capital_governor_policy,
    max_eligible_capital_tier_for_promotion,
)
from trading_ai.global_layer.deterministic_autonomous_orchestration import (
    assert_single_live_capital_consumer,
    run_auto_promotion_cycle,
    run_capital_scale_up_cycle,
)
from trading_ai.global_layer.execution_authority import grant_execution_authority, revoke_execution_authority
from trading_ai.global_layer.orchestration_paths import bot_auto_promotion_truth_path
from trading_ai.global_layer.orchestration_schema import CapitalAuthorityTier, PermissionLevel, PromotionTier
from trading_ai.global_layer.promotion_contract_engine import evaluate_promotion_contract_detailed


@pytest.fixture
def pc_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(tmp_path / "registry.json"))
    gdir = tmp_path / "gov"
    gdir.mkdir(parents=True, exist_ok=True)

    def _gov() -> Path:
        return gdir

    monkeypatch.setattr("trading_ai.global_layer._bot_paths.global_layer_governance_dir", _gov)
    monkeypatch.setattr("trading_ai.global_layer.orchestration_paths.global_layer_governance_dir", _gov)
    return tmp_path


def _qualified_scorecard_t1() -> dict:
    return {
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
    }


def test_promotion_deterministic_pass_and_staged_only(pc_env: Path) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "bot_promo_test",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "last_heartbeat_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "promotion_scorecard": _qualified_scorecard_t1(),
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
    out = run_auto_promotion_cycle(registry_path=regp)
    assert bot_auto_promotion_truth_path().is_file()
    b = get_bot("bot_promo_test", path=regp)
    assert b is not None
    assert str(b.get("promotion_tier")) == PromotionTier.T1.value
    assert str(b.get("permission_level")) == PermissionLevel.ADVISORY_ONLY.value


def test_promotion_no_self_cert_when_external_required(pc_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    gov = pc_env / "gov" / "orchestration" / "promotion_contract_policy.json"
    gov.parent.mkdir(parents=True, exist_ok=True)
    bundled = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "trading_ai"
        / "global_layer"
        / "_governance_data"
        / "orchestration"
        / "promotion_contract_policy.json"
    )
    pol = json.loads(bundled.read_text(encoding="utf-8"))
    pol["require_external_eval_signals"] = True
    gov.write_text(json.dumps(pol, indent=2), encoding="utf-8")

    register_bot(
        {
            "bot_id": "bot_ext",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "last_heartbeat_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "promotion_scorecard": _qualified_scorecard_t1(),
            "governance_flags": {"ceo_review_pass": True, "risk_review_pass": True},
            "external_eval_signals": {k: False for k in ("performance_evaluator_ok", "risk_engine_ok", "truth_layer_ok", "orchestration_policy_ok")},
        },
        path=regp,
    )
    b = get_bot("bot_ext", path=regp)
    ev = evaluate_promotion_contract_detailed(b, policy=pol)
    assert ev.get("passed") is False
    assert "external" in str(ev.get("blocker") or "").lower() or any(
        "external" in k for k in (ev.get("clauses") or {}).keys()
    )


def test_capital_no_jump_on_promotion_alone(pc_env: Path) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "bot_cap",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "promotion_tier": PromotionTier.T3.value,
            "capital_authority_tier": CapitalAuthorityTier.C0.value,
            "last_heartbeat_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "promotion_scorecard": _qualified_scorecard_t1(),
            "governance_flags": {"ceo_review_pass": True, "risk_review_pass": True},
        },
        path=regp,
    )
    b = get_bot("bot_cap", path=regp)
    assert max_eligible_capital_tier_for_promotion(PromotionTier.T3.value) == CapitalAuthorityTier.C2.value
    ok, why, _ = check_live_quote_allowed(b, 10.0, avenue="A", gate="gate_a")
    assert ok is False
    assert "C0" in why or "capital_tier" in why


def test_capital_scale_up_one_step(pc_env: Path) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "bot_scale",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "promotion_tier": PromotionTier.T4.value,
            "capital_authority_tier": CapitalAuthorityTier.C0.value,
            "promotion_scorecard": {
                **_qualified_scorecard_t1(),
                "clean_live_cycles": 25,
                "max_drawdown_pct": 2.0,
                "expectancy": 0.05,
            },
        },
        path=regp,
    )
    out = run_capital_scale_up_cycle(registry_path=regp)
    assert out.get("applied")
    b = get_bot("bot_scale", path=regp)
    assert str(b.get("capital_authority_tier")) == CapitalAuthorityTier.C1.value


def test_single_execution_authority_align_and_mismatch(pc_env: Path) -> None:
    grant_execution_authority(
        bot_id="bot_x",
        avenue="A",
        gate="gate_a",
        route="default",
        contract_ref="t",
        approved_by="CEO",
    )
    ok, why = assert_single_live_capital_consumer("A", "gate_a", "default", "bot_x")
    assert ok is True and why == "ok"
    grant_execution_authority(
        bot_id="other",
        avenue="A",
        gate="gate_a",
        route="default",
        contract_ref="t2",
        approved_by="CEO",
    )
    ok2, why2 = assert_single_live_capital_consumer("A", "gate_a", "default", "bot_x")
    assert ok2 is False
    assert "mismatch" in why2
    revoke_execution_authority("A", "gate_a", "default", reason="test_cleanup")


def test_oversized_quote_blocked_when_capital_positive(pc_env: Path) -> None:
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    register_bot(
        {
            "bot_id": "bot_live",
            "role": BotRole.SCANNER.value,
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "lifecycle_state": BotLifecycleState.SHADOW.value,
            "promotion_tier": PromotionTier.T4.value,
            "capital_authority_tier": CapitalAuthorityTier.C2.value,
            "promotion_scorecard": _qualified_scorecard_t1(),
        },
        path=regp,
    )
    b = get_bot("bot_live", path=regp)
    ok, why, _ = check_live_quote_allowed(b, 1_000_000.0, avenue="A", gate="gate_a")
    assert ok is False
    assert "exceeds" in why or "cap" in why


def test_evaluate_capital_scale_up_denied_on_drawdown(pc_env: Path) -> None:
    pol = load_capital_governor_policy()
    bot = {
        "bot_id": "x",
        "promotion_tier": PromotionTier.T5.value,
        "capital_authority_tier": CapitalAuthorityTier.C1.value,
        "promotion_scorecard": {"clean_live_cycles": 100, "max_drawdown_pct": 50.0, "expectancy": 1.0},
    }
    ev = evaluate_capital_scale_up(bot, policy=pol)
    assert ev.get("allowed") is False
    assert any("drawdown" in r for r in (ev.get("reasons") or []))
