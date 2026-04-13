"""
Mandatory 15-step execution chain — identical for compounding and gap exploitation.
Gap mode only changes sizing inputs at Step 5. No time-of-day restrictions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trading_ai.governance import system_doctrine as doctrine
from trading_ai.governance.position_sizing_policy import default_caps_for_capital
from trading_ai.governance.storage_architecture import append_shark_audit_record
from trading_ai.shark.capital_phase import detect_phase, phase_params
from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.execution_live import ezras_dry_run_from_env
from trading_ai.shark.executor import build_execution_intent
from trading_ai.shark.models import ExecutionIntent, ScoredOpportunity
from trading_ai.shark.risk_context import build_risk_context
from trading_ai.shark.reporting import alert_trade_fired
from trading_ai.shark.state import BAYES, MANDATE
from trading_ai.shark.state_store import load_capital, save_bayesian_snapshot, save_capital


@dataclass
class ChainResult:
    ok: bool
    halted_at: str
    audit: List[Dict[str, Any]] = field(default_factory=list)
    intent: Optional[ExecutionIntent] = None
    execution_delay_seconds: float = 0.0


def _append(audit: List[Dict[str, Any]], step: str, **kw: Any) -> None:
    audit.append({"step": step, **kw})


def _resolve_execute_live(explicit: Optional[bool]) -> bool:
    """Live venue submit unless ``explicit is False`` or ``EZRAS_DRY_RUN`` is truthy."""
    if explicit is not None:
        return explicit
    load_shark_dotenv()
    return not ezras_dry_run_from_env()


def run_execution_chain(
    scored: ScoredOpportunity,
    *,
    capital: float,
    outlet: str,
    peak_capital: Optional[float] = None,
    gap_exploitation_mode: bool = False,
    current_gap_exposure_fraction: float = 0.0,
    estimated_execution_delay_seconds: float = 0.0,
    fee_to_edge_ratio: float = 0.0,
    last_trade_unix: Optional[float] = None,
    now_unix: Optional[float] = None,
    strategy_key: str = "shark_default",
    doctrine_context_extra: Optional[Dict[str, Any]] = None,
    execute_live: Optional[bool] = None,
) -> ChainResult:
    audit: List[Dict[str, Any]] = []
    run_live = _resolve_execute_live(execute_live)
    now = now_unix or time.time()
    pk = peak_capital if peak_capital is not None else capital
    phase = detect_phase(capital)
    pp = phase_params(phase)
    risk = build_risk_context(
        current_capital=capital,
        peak_capital=pk,
        base_min_edge=pp.min_edge,
        last_trade_unix=last_trade_unix,
        now_unix=now,
    )

    intent = build_execution_intent(
        scored,
        capital=capital,
        outlet=outlet,
        gap_exploitation_mode=gap_exploitation_mode,
        current_gap_exposure_fraction=current_gap_exposure_fraction,
        min_edge_effective=risk.effective_min_edge,
        risk_position_multiplier=risk.position_size_multiplier,
        market_category=scored.market.market_category,
        strategy_key=strategy_key,
    )
    if intent is None:
        _append(audit, "1_precheck", skipped=True, reason="no_intent_tier_or_edge")
        return ChainResult(False, "precheck", audit, None)

    edge = intent.edge_after_fees

    # Step 1 — Doctrine gate
    ctx = doctrine.DoctrineContext(
        source=intent.source,
        mandate_compounding_paused=MANDATE.compounding_paused,
        mandate_gaps_paused=MANDATE.gaps_paused,
        execution_paused=MANDATE.execution_paused,
        edge_after_fees=edge,
        min_edge_for_phase=risk.effective_min_edge,
        anti_forced_trade=True,
        cluster_paused=False,
        tags={"gap_exploit": intent.gap_exploit, **(doctrine_context_extra or {})},
    )
    dr = doctrine.check_doctrine_gate(ctx)
    _append(audit, "1_doctrine_gate", ok=dr.ok, reason=dr.reason)
    if not dr.ok:
        return ChainResult(False, "doctrine", audit, intent)

    # Step 2 — Phase limit
    _append(audit, "2_phase_limit", phase=phase.value, min_edge_effective=risk.effective_min_edge)
    if edge < risk.effective_min_edge:
        return ChainResult(False, "phase_limit", audit, intent)

    # Step 3 — Global drawdown check (informational; sizing already in intent)
    _append(
        audit,
        "3_global_drawdown",
        drawdown=risk.drawdown_from_peak,
        position_scale=risk.position_size_multiplier,
        idle_widen=risk.idle_capital_over_6h,
    )

    # Step 4 — Loss cluster (sizing applied in executor; log status)
    _append(audit, "4_loss_cluster", note="multiplier_applied_in_step_5")

    # Step 5 — Position size (Kelly × phase × tier × cluster × DD)
    _append(audit, "5_position_size", fraction=intent.stake_fraction_of_capital)

    caps = default_caps_for_capital(capital)
    if intent.stake_fraction_of_capital > caps.max_fraction_of_capital + 1e-9:
        _append(audit, "5_position_size", fail=True, cap=caps.max_fraction_of_capital)
        return ChainResult(False, "position_cap", audit, intent)

    # Step 6 — Fee-adjusted profitability
    if edge > 0 and fee_to_edge_ratio > 0.40:
        _append(audit, "6_fees_kill_edge", ratio=fee_to_edge_ratio)
        return ChainResult(False, "fee_kill", audit, intent)
    _append(audit, "6_fee_check", ok=True, fee_to_edge_ratio=fee_to_edge_ratio)

    # Step 7 — Execution delay / latency kill
    erosion = estimated_execution_delay_seconds * 0.001
    if edge > 0 and erosion / edge > 0.40:
        _append(audit, "7_latency_kill", erosion_ratio=erosion / edge)
        return ChainResult(False, "latency_kill", audit, intent)
    _append(audit, "7_execution_delay", ok=True, seconds=estimated_execution_delay_seconds)

    if not getattr(intent, "is_mana", False):
        try:
            alert_trade_fired(
                hunt_types=[h.value for h in intent.hunt_types],
                edge=intent.edge_after_fees,
                position_fraction=intent.stake_fraction_of_capital,
                capital=capital,
                tier=str(intent.meta.get("tier", "B")),
                outlet=intent.outlet,
                market_desc=str(intent.market_id),
                resolves_in="TBD",
            )
        except Exception:
            logging.getLogger(__name__).debug("alert_trade_fired failed", exc_info=True)

    # Step 8 — Log submission intent JSONL
    _append(audit, "8_log_intent", market_id=intent.market_id)
    append_shark_audit_record({"step": "intent", "intent": intent.meta | {"market_id": intent.market_id}})

    if run_live:
        from trading_ai.shark import execution_live as el
        from trading_ai.shark.reporting import send_telegram_live

        try:
            order_res = el.submit_order(intent)
        except Exception as exc:
            logging.getLogger(__name__).exception("submit_order failed")
            _append(audit, "9_submit_order", status="FAILED", error=str(exc))
            append_shark_audit_record({"step": "submit_failed", "market_id": intent.market_id, "error": str(exc)})
            if not getattr(intent, "is_mana", False):
                try:
                    send_telegram_live(f"❌ ORDER FAILED\n{intent.outlet} {intent.market_id}\n{exc!s}")
                except Exception:
                    pass
            return ChainResult(False, "submit_failed", audit, intent)

        _append(audit, "9_submit_order", status="submitted", order_id=order_res.order_id)
        append_shark_audit_record({"step": "submit", "market_id": intent.market_id, "order_id": order_res.order_id})

        conf = el.confirm_execution(order_res, intent)
        _append(
            audit,
            "10_confirm_fill",
            confirmed=conf.confirmed,
            slippage_pct=conf.slippage_pct,
            high_slippage=conf.high_slippage_warning,
            unfilled_cancelled=conf.unfilled_cancelled,
        )
        append_shark_audit_record(
            {
                "step": "fill",
                "market_id": intent.market_id,
                "slippage_pct": conf.slippage_pct,
                "high_slippage_warning": conf.high_slippage_warning,
            }
        )
        if not conf.confirmed:
            return ChainResult(False, "unfilled", audit, intent)

        pos = el.build_open_position_from_intent(intent, order_res, conf)
        el.monitor_position(pos)
        _append(audit, "11_monitor_resolution", position_id=pos.position_id, poll_seconds=60)
        _append(audit, "12_log_outcome", status="open_position_tracked")
    else:
        _append(audit, "9_submit_order", status="dry_run_stub")
        append_shark_audit_record({"step": "submit", "market_id": intent.market_id})
        _append(audit, "10_confirm_fill", status="dry_run_stub")
        append_shark_audit_record({"step": "fill", "market_id": intent.market_id})
        _append(audit, "11_monitor_resolution", poll_seconds=60)
        _append(audit, "12_log_outcome", status="pending_external")

    # Step 13 — Post-trade hooks (Bayesian etc.)
    _append(audit, "13_post_trade_hooks", bayesian="pending_resolution_callback")

    # Step 14 — Update capital.json (on closed trade only in production)
    _append(audit, "14_capital_json", note="updated_on_close")

    # Step 15 — Telegram resolution alert (on close)
    _append(audit, "15_telegram_resolution", note="on_close")

    return ChainResult(True, "complete", audit, intent, estimated_execution_delay_seconds)


def trigger_bayesian_after_resolution(
    *,
    strategy: str,
    hunt_types: list,
    outlet: str,
    win: bool,
    hour_utc: Optional[int] = None,
) -> None:
    BAYES.update_from_trade(
        strategy=strategy,
        hunt_types=hunt_types,
        outlet=outlet,
        win=win,
        hour_utc=hour_utc,
    )
    save_bayesian_snapshot()


def hook_post_trade_resolution(
    trade_id: str,
    *,
    win: bool,
    strategy: str,
    hunt_types: list,
    outlet: str,
    market_id: str,
    market_category: str = "default",
    hour_utc: Optional[int] = None,
    pnl_dollars: Optional[float] = None,
    update_capital: bool = True,
    is_mana: bool = False,
) -> None:
    from trading_ai.shark.models import HuntType
    from trading_ai.shark.state import LOSS_TRACKER

    hts = [h if isinstance(h, HuntType) else HuntType(h) for h in hunt_types]
    trigger_bayesian_after_resolution(
        strategy=strategy, hunt_types=hts, outlet=outlet, win=win, hour_utc=hour_utc
    )
    if not is_mana:
        LOSS_TRACKER.record_outcome(
            strategy=strategy,
            hunt_type=hts[0],
            outlet=outlet,
            market_category=market_category,
            win=win,
        )
    if pnl_dollars is not None and update_capital and not is_mana:
        from trading_ai.shark.state_store import apply_win_loss_to_capital

        apply_win_loss_to_capital(pnl_dollars)
    _ = trade_id


def refresh_capital_snapshot_after_external_update() -> None:
    """If capital updated elsewhere, re-save phase line."""
    rec = load_capital()
    save_capital(rec)
