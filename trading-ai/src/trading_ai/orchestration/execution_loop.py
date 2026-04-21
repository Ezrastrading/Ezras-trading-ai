"""Avenue-agnostic execution loop skeleton — wires stages; venue logic via injected adapters."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, Optional

from trading_ai.orchestration.lifecycle_events import CANONICAL_LOOP_ORDER, LoopStage, new_event_envelope
from trading_ai.orchestration.rebuy_eligibility import evaluate_rebuy_eligibility

logger = logging.getLogger(__name__)


class ExecutionLoopContext:
    """Per-round-trip state holder (no global mutable singleton)."""

    def __init__(self, *, avenue_id: str, trading_gate: str) -> None:
        self.avenue_id = avenue_id
        self.trading_gate = trading_gate
        self.trade_id = f"loop_{uuid.uuid4().hex[:16]}"
        self.stages_completed: list[str] = []
        self.payloads: Dict[str, Any] = {}


StageFn = Callable[[ExecutionLoopContext], Dict[str, Any]]


def run_loop_stage(
    ctx: ExecutionLoopContext,
    stage: LoopStage,
    handler: Optional[StageFn],
) -> Dict[str, Any]:
    env = new_event_envelope(stage=stage, avenue_id=ctx.avenue_id, trade_id=ctx.trade_id, gate=ctx.trading_gate)
    if handler is None:
        env["result"] = "no_handler_staged_only"
        ctx.stages_completed.append(stage.value)
        return env
    try:
        out = handler(ctx)
        env["result"] = out
        ctx.stages_completed.append(stage.value)
        ctx.payloads[stage.value] = out
        return env
    except Exception as exc:
        logger.exception("loop stage %s failed", stage.value)
        env["error"] = str(exc)
        return env


def full_chain_wired_report(*, avenue_id: str) -> Dict[str, Any]:
    """Honest static report: which stages have in-repo handlers vs stub."""
    stubs = {"A": "coinbase_engine_and_outlets", "B": "kalshi_outlets_partial", "C": "none_scaffold"}
    return {
        "avenue_id": avenue_id,
        "canonical_order": CANONICAL_LOOP_ORDER,
        "note": "Handlers are registered per deployment; default runner uses tick-only without live handlers unless injected.",
        "integration_stub": stubs.get(avenue_id, "unknown"),
    }


def rebuy_gate(
    *,
    prior_ok: bool,
    log_ok: bool,
    recon_ok: bool,
    gov_ok: bool,
    adapt_ok: bool,
    halted: bool,
    dup: bool,
    cool: bool,
) -> Dict[str, Any]:
    ev = evaluate_rebuy_eligibility(
        prior_round_trip_finalized=prior_ok,
        logging_succeeded=log_ok,
        reconciliation_ok_or_classified=recon_ok,
        governance_recheck_ok=gov_ok,
        adaptive_recheck_ok=adapt_ok,
        failsafe_halted=halted,
        duplicate_would_block=dup,
        avenue_cooldown_active=cool,
    )
    return {
        "rebuy_allowed": ev.rebuy_allowed,
        "reason_codes": ev.reason_codes,
        "next_candidate_source": ev.next_candidate_source,
        "blocked_by_adaptive": ev.blocked_by_adaptive,
        "blocked_by_governance": ev.blocked_by_governance,
    }
