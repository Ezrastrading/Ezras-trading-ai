"""Orchestration safety backbone — gate, idempotency, drift, conflicts, CEO budget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_registry import register_bot
from trading_ai.global_layer.bot_types import BotLifecycleState, BotRole
from trading_ai.global_layer.budget_governor import save_budget_state
from trading_ai.global_layer.execution_authority import grant_execution_authority, revoke_execution_authority
from trading_ai.global_layer.execution_intent_idempotency import claim_execution_intent, deterministic_intent_id
from trading_ai.global_layer.orchestration_authority_drift import detect_authority_drift
from trading_ai.global_layer.orchestration_detection import resolve_conflicting_signals
from trading_ai.global_layer.orchestration_live_execution_gate import evaluate_live_execution_gate
from trading_ai.global_layer.orchestration_permissions import assert_no_self_promotion, bot_may_place_live_orders
from trading_ai.global_layer.orchestration_schema import PermissionLevel
from trading_ai.global_layer.orchestration_truth_chain import build_orchestration_truth_chain


def _minimal_exec_bot(bid: str) -> dict:
    return {
        "bot_id": bid,
        "role": BotRole.EXECUTION.value,
        "avenue": "A",
        "gate": "gate_a",
        "version": "v1",
        "lifecycle_state": BotLifecycleState.SHADOW.value,
        "permission_level": PermissionLevel.SHADOW_EXECUTION.value,
        "performance": {},
    }


def test_shadow_bot_cannot_place_live_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    register_bot(_minimal_exec_bot("bot_shadow_ex"), path=regp)
    from trading_ai.global_layer.bot_registry import get_bot

    b = get_bot("bot_shadow_ex", path=regp)
    ok, why = bot_may_place_live_orders(b or {})
    assert ok is False
    assert "permission" in why or "denies" in why


def test_idempotency_duplicate_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(
        "trading_ai.global_layer.execution_intent_idempotency.execution_intent_ledger_path",
        lambda: ledger,
    )
    iid = deterministic_intent_id(
        bot_id="b1",
        signal_time_iso="2026-01-01T00:00:00+00:00",
        symbol="BTC-USD",
        intent="buy",
        avenue="A",
        gate="gate_a",
    )
    ok1, w1, _ = claim_execution_intent(iid, meta={"t": 1})
    ok2, w2, _ = claim_execution_intent(iid, meta={"t": 2})
    assert ok1 is True and w1 == "ok"
    assert ok2 is False and w2 == "duplicate_intent"


def test_signal_conflict_no_trade() -> None:
    out = resolve_conflicting_signals(
        [
            {"bot_id": "a", "side": "buy", "symbol": "BTC-USD"},
            {"bot_id": "b", "side": "sell", "symbol": "BTC-USD"},
        ]
    )
    assert out["action"] == "no_trade"


def test_self_promotion_forbidden() -> None:
    with pytest.raises(ValueError, match="self_promotion"):
        assert_no_self_promotion("observe_only", "execution_authority", "bot_x", "bot_x")


def test_authority_drift_detects_registry_without_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    b = _minimal_exec_bot("bot_claim")
    b["permission_level"] = PermissionLevel.EXECUTION_AUTHORITY.value
    register_bot(b, path=regp)
    d = detect_authority_drift(registry_path=regp)
    assert d.get("blocked") is True
    kinds = [f.get("kind") for f in d.get("findings") or []]
    assert "registry_claim_without_slot" in kinds


def test_live_execution_gate_denies_without_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    monkeypatch.setenv("EZRAS_ORCHESTRATION_LIVE_GATE", "1")
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(
        "trading_ai.global_layer.execution_intent_idempotency.execution_intent_ledger_path",
        lambda: ledger,
    )
    register_bot(_minimal_exec_bot("bot_gate"), path=regp)
    from trading_ai.global_layer.bot_registry import get_bot

    bot = get_bot("bot_gate", path=regp)
    out = evaluate_live_execution_gate(
        bot or {},
        quote_usd=10.0,
        avenue="A",
        gate="gate_a",
        force_check=True,
        skip_idempotency_claim=True,
    )
    assert out.get("allowed") is False


def test_live_execution_gate_holds_with_grant_and_capital(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    monkeypatch.setenv("EZRAS_ORCHESTRATION_LIVE_GATE", "1")
    monkeypatch.setenv("EZRAS_ORCHESTRATION_STRICT_AUTHORITY", "1")
    auth_path = tmp_path / "execution_authority.json"
    monkeypatch.setattr(
        "trading_ai.global_layer.execution_authority.execution_authority_path",
        lambda: auth_path,
    )
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(
        "trading_ai.global_layer.execution_intent_idempotency.execution_intent_ledger_path",
        lambda: ledger,
    )
    caps_path = tmp_path / "orchestration_risk_caps.json"
    caps_path.write_text(
        json.dumps(
            {
                "truth_version": "orchestration_risk_caps_v1",
                "max_daily_loss_usd_global": 999999.0,
                "current_daily_realized_loss_usd": 0.0,
                "max_data_age_sec_for_trading": 9999,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_risk_caps.orchestration_risk_caps_path",
        lambda: caps_path,
    )
    b = _minimal_exec_bot("bot_live")
    b["permission_level"] = PermissionLevel.EXECUTION_AUTHORITY.value
    b["capital_authority_tier"] = "C2"
    b["capital_ramp_complete"] = True
    register_bot(b, path=regp)
    grant_execution_authority(
        bot_id="bot_live",
        avenue="A",
        gate="gate_a",
        route="default",
        contract_ref="test_contract",
        approved_by="test",
    )
    from trading_ai.global_layer.bot_registry import get_bot

    bot = get_bot("bot_live", path=regp)
    out = evaluate_live_execution_gate(
        bot or {},
        quote_usd=10.0,
        avenue="A",
        gate="gate_a",
        symbol="BTC-USD",
        intent_label="buy",
        signal_time_iso="2026-04-20T12:00:00+00:00",
        registry_path=regp,
        force_check=True,
    )
    assert out.get("allowed") is True
    revoke_execution_authority("A", "gate_a", "default", reason="test_cleanup")


def test_truth_chain_contains_blockers_when_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    ks_path = tmp_path / "orchestration_kill_switch.json"
    ks_path.parent.mkdir(parents=True, exist_ok=True)
    ks_path.write_text(
        json.dumps(
            {
                "truth_version": "orchestration_kill_switch_v1",
                "orchestration_frozen": True,
                "avenue": {},
                "gate": {},
                "bot_class": {},
                "bot_id": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: ks_path,
    )
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path / "rt"))
    (tmp_path / "rt").mkdir()
    register_bot(_minimal_exec_bot("b1"), path=regp)
    chain = build_orchestration_truth_chain(registry_path=regp)
    assert "global_orchestration_frozen" in (chain.get("blockers") or [])


def test_ceo_review_respects_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regp = tmp_path / "bot_registry.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(regp))
    bg = tmp_path / "budget.json"
    monkeypatch.setattr("trading_ai.global_layer.budget_governor.budget_state_path", lambda: bg)
    save_budget_state(
        {
            "truth_version": "budget_governor_v1",
            "global_daily_token_budget": 250_000,
            "per_ceo_review_token_budget": 100,
            "ceo_review_tokens_used_today": 99,
            "review_day_id": "20990101",
            "global_token_used": 0,
        }
    )
    register_bot(_minimal_exec_bot("b2"), path=regp)
    from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review

    out = write_daily_ceo_review(registry_path=regp, estimated_review_tokens=500)
    assert out["review_budget"]["allowed"] is False
