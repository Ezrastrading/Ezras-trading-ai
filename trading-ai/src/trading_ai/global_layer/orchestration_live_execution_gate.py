"""
Unified **orchestration** gate for live venue intents (additive).

Does not replace Gate A/B proof contracts or venue clients. Call this from execution paths when
``EZRAS_ORCHESTRATION_LIVE_GATE=1`` (or pass ``force_check=True`` in tests).

Order of checks: kill switch → lifecycle → orchestration block → permission + authority slot →
capital governor → daily loss → idempotency claim → optional data-age.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.global_layer.bot_types import BotLifecycleState
from trading_ai.global_layer.capital_governor import check_live_quote_allowed
from trading_ai.global_layer.deterministic_autonomous_orchestration import assert_single_live_capital_consumer
from trading_ai.global_layer.execution_intent_idempotency import claim_execution_intent, deterministic_intent_id
from trading_ai.global_layer.orchestration_kill_switch import orchestration_blocked_for_bot
from trading_ai.global_layer.orchestration_permissions import bot_may_place_live_orders
from trading_ai.global_layer.orchestration_risk_caps import check_daily_loss_halt, load_orchestration_risk_caps
from trading_ai.global_layer.orchestration_schema import OrchestrationBotStatus


def _gate_enabled(*, force_check: bool) -> bool:
    if force_check:
        return True
    return (os.environ.get("EZRAS_ORCHESTRATION_LIVE_GATE") or "").strip().lower() in ("1", "true", "yes")


def _lifecycle_blocks_live(bot: Dict[str, Any]) -> Tuple[bool, str]:
    life = str(bot.get("lifecycle_state") or "")
    if life in (
        BotLifecycleState.PROPOSED.value,
        BotLifecycleState.RETIRED.value,
        BotLifecycleState.ARCHIVED.value,
        BotLifecycleState.FROZEN.value,
    ):
        return True, f"lifecycle_blocks:{life}"
    if str(bot.get("status") or "") == OrchestrationBotStatus.DISABLED.value:
        return True, "bot_status_disabled"
    return False, "ok"


def check_data_freshness_for_trading(*, data_age_sec: Optional[float] = None) -> Tuple[bool, str]:
    """If ``data_age_sec`` is None, pass (caller did not measure)."""
    if data_age_sec is None:
        return True, "data_age_not_provided_ok"
    caps = load_orchestration_risk_caps()
    mx = float(caps.get("max_data_age_sec_for_trading") or 1e9)
    if data_age_sec > mx:
        return False, f"data_stale:{data_age_sec}>{mx}"
    return True, "ok"


def evaluate_live_execution_gate(
    bot: Dict[str, Any],
    *,
    quote_usd: float,
    avenue: str,
    gate: str,
    route: str = "default",
    symbol: str = "",
    intent_label: str = "open",
    signal_time_iso: Optional[str] = None,
    intent_id: Optional[str] = None,
    data_age_sec: Optional[float] = None,
    registry_path: Optional[Path] = None,
    force_check: bool = False,
    skip_idempotency_claim: bool = False,
) -> Dict[str, Any]:
    """
    Returns a single JSON-serializable dict with ``allowed`` bool and ``reason`` string.
    """
    out: Dict[str, Any] = {
        "truth_version": "orchestration_live_execution_gate_v1",
        "allowed": False,
        "reason": "not_evaluated",
        "checks": {},
    }
    if not _gate_enabled(force_check=force_check):
        out["allowed"] = True
        out["reason"] = "orchestration_gate_disabled_by_env"
        out["honesty"] = "Set EZRAS_ORCHESTRATION_LIVE_GATE=1 to enforce this layer in production."
        return out

    blocked, why = orchestration_blocked_for_bot(bot)
    out["checks"]["orchestration_kill_switch"] = {"ok": not blocked, "reason": why}
    if blocked:
        out["reason"] = why
        return out

    lb, lr = _lifecycle_blocks_live(bot)
    out["checks"]["lifecycle"] = {"ok": not lb, "reason": lr}
    if lb:
        out["reason"] = lr
        return out

    may, pr = bot_may_place_live_orders(bot)
    out["checks"]["permission_and_slot"] = {"ok": may, "reason": pr}
    if not may:
        out["reason"] = pr
        return out

    strict = (os.environ.get("EZRAS_ORCHESTRATION_STRICT_AUTHORITY") or "1").strip().lower() in ("1", "true", "yes")
    if strict:
        bid = str(bot.get("bot_id") or "")
        ac_ok, ac_why = assert_single_live_capital_consumer(avenue, gate, route, bid, registry_path=registry_path)
        out["checks"]["single_authority_alignment"] = {"ok": ac_ok, "reason": ac_why}
        if not ac_ok:
            out["reason"] = ac_why
            return out

    cap_ok, cap_why, cap_diag = check_live_quote_allowed(bot, quote_usd, avenue=avenue, gate=gate, route=route)
    out["checks"]["capital_governor"] = {"ok": cap_ok, "reason": cap_why, "diagnostics": cap_diag}
    if not cap_ok:
        out["reason"] = cap_why
        return out

    dl_ok, dl_why, dl_diag = check_daily_loss_halt()
    out["checks"]["daily_loss"] = {"ok": dl_ok, "reason": dl_why, "diagnostics": dl_diag}
    if not dl_ok:
        out["reason"] = dl_why
        return out

    st = signal_time_iso or datetime.now(timezone.utc).isoformat()
    iid = intent_id or deterministic_intent_id(
        bot_id=str(bot.get("bot_id") or ""),
        signal_time_iso=st,
        symbol=symbol or "UNKNOWN",
        intent=intent_label,
        avenue=avenue,
        gate=gate,
        route=route,
    )
    out["intent_id"] = iid
    if not skip_idempotency_claim:
        ok_claim, claim_why, claim_row = claim_execution_intent(
            iid,
            meta={
                "avenue": avenue,
                "gate": gate,
                "route": route,
                "quote_usd": quote_usd,
                "symbol": symbol,
            },
        )
        out["checks"]["idempotency"] = {"ok": ok_claim, "reason": claim_why, "row": claim_row}
        if not ok_claim:
            out["reason"] = claim_why
            return out

    fresh_ok, fr_why = check_data_freshness_for_trading(data_age_sec=data_age_sec)
    out["checks"]["data_freshness"] = {"ok": fresh_ok, "reason": fr_why}
    if not fresh_ok:
        out["reason"] = fr_why
        return out

    out["allowed"] = True
    out["reason"] = "ok"
    return out
