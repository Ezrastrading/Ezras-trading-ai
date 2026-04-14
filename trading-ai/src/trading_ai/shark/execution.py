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
from trading_ai.shark.models import ExecutionIntent, HuntType, ScoredOpportunity
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
    log = logging.getLogger(__name__)
    run_live = _resolve_execute_live(execute_live)
    log.info(
        "Execution chain started: market=%s outlet=%s execute_live=%s resolved_run_live=%s",
        scored.market.market_id,
        outlet,
        execute_live,
        run_live,
    )
    audit: List[Dict[str, Any]] = []
    if run_live:
        log.info("execute_live=True (dry_run=false)")
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

    blend_exec_min_edge = risk.effective_min_edge
    if scored.edge_size > 1e-12:
        blend_exec_min_edge = min(risk.effective_min_edge, scored.edge_size)

    intent = build_execution_intent(
        scored,
        capital=capital,
        outlet=outlet,
        gap_exploitation_mode=gap_exploitation_mode,
        current_gap_exposure_fraction=current_gap_exposure_fraction,
        min_edge_effective=blend_exec_min_edge,
        risk_position_multiplier=risk.position_size_multiplier,
        market_category=scored.market.market_category,
        strategy_key=strategy_key,
        current_drawdown_pct=risk.drawdown_from_peak,
    )
    if intent is None:
        u0 = scored.market.underlying_data_if_available or {}
        log.info(
            "Precheck FAILED intent=None: market=%s outlet=%s tier=%s edge_size=%.6f "
            "blend_min_edge_used=%.6f risk_effective_min_edge=%.6f phase_base_min_edge=%.6f hunts=%s "
            "yes_token_id=%s no_token_id=%s condition_id=%s",
            scored.market.market_id,
            outlet,
            scored.tier.value,
            scored.edge_size,
            blend_exec_min_edge,
            risk.effective_min_edge,
            pp.min_edge,
            [h.hunt_type.value for h in scored.hunts],
            u0.get("yes_token_id"),
            u0.get("no_token_id"),
            u0.get("condition_id"),
        )
        _append(audit, "1_precheck", skipped=True, reason="no_intent_tier_or_edge")
        return ChainResult(False, "precheck", audit, None)

    if (outlet or "").strip().lower() == "kalshi":
        from trading_ai.shark import kalshi_limits

        n_kalshi_open = kalshi_limits.count_kalshi_open_positions()
        if HuntType.NEAR_RESOLUTION_HV in intent.hunt_types:
            max_k = kalshi_limits.kalshi_hv_max_open_positions()
        else:
            max_k = kalshi_limits.kalshi_max_open_positions_from_env()
        if n_kalshi_open >= max_k:
            _append(
                audit,
                "0_kalshi_open_cap",
                open_positions=n_kalshi_open,
                max_open=max_k,
            )
            log.info("Kalshi execution skipped: open cap %s >= %s", n_kalshi_open, max_k)
            return ChainResult(False, "kalshi_max_open", audit, intent)

    if (outlet or "").strip().lower() == "polymarket":
        from trading_ai.shark.outlets.polymarket import ensure_polymarket_intent_token_ids

        if not ensure_polymarket_intent_token_ids(intent, scored.market):
            log.warning(
                "Polymarket skip — missing token_id after CLOB backfill: market=%s token_id=%s yes_token=%s no_token=%s pure_dual=%s",
                scored.market.market_id,
                (intent.meta or {}).get("token_id"),
                (intent.meta or {}).get("yes_token_id"),
                (intent.meta or {}).get("no_token_id"),
                bool((intent.meta or {}).get("pure_arbitrage_dual")),
            )
            _append(audit, "1_token_resolution", skipped=True, reason="polymarket_missing_token_id")
            return ChainResult(False, "polymarket_no_tokens", audit, intent)

    log.info(
        "Precheck OK: token_id=%s yes_token=%s no_token=%s outlet=%s side=%s shares=%s market=%s",
        (intent.meta or {}).get("token_id"),
        (intent.meta or {}).get("yes_token_id"),
        (intent.meta or {}).get("no_token_id"),
        intent.outlet,
        intent.side,
        intent.shares,
        intent.market_id,
    )

    edge = intent.edge_after_fees

    # Step 1 — Doctrine gate
    ctx = doctrine.DoctrineContext(
        source=intent.source,
        mandate_compounding_paused=MANDATE.compounding_paused,
        mandate_gaps_paused=MANDATE.gaps_paused,
        execution_paused=doctrine.is_execution_paused(),
        edge_after_fees=edge,
        min_edge_for_phase=risk.effective_min_edge,
        anti_forced_trade=True,
        cluster_paused=False,
        tags={"gap_exploit": intent.gap_exploit, **(doctrine_context_extra or {})},
    )
    dr = doctrine.check_doctrine_gate(ctx)
    _append(audit, "1_doctrine_gate", ok=dr.ok, reason=dr.reason)
    if not dr.ok:
        log.info("Gate 1 doctrine: FAIL reason=%s", dr.reason)
        return ChainResult(False, "doctrine", audit, intent)
    log.info("Gate 1 doctrine: PASS")

    # Step 2 — Phase limit (use same blend as intent build so scan-qualified edges are not double-blocked)
    _append(audit, "2_phase_limit", phase=phase.value, min_edge_effective=blend_exec_min_edge)
    if edge < blend_exec_min_edge:
        log.info("Gate 2 phase: FAIL edge=%.4f min=%.4f", edge, blend_exec_min_edge)
        return ChainResult(False, "phase_limit", audit, intent)
    log.info("Gate 2 phase: PASS")

    # Step 3 — Global drawdown check (informational; sizing already in intent)
    _append(
        audit,
        "3_global_drawdown",
        drawdown=risk.drawdown_from_peak,
        position_scale=risk.position_size_multiplier,
        idle_widen=risk.idle_capital_over_6h,
    )
    log.info("Gate 3 global_drawdown: PASS (informational)")

    # Step 4 — Loss cluster (sizing applied in executor; log status)
    _append(audit, "4_loss_cluster", note="multiplier_applied_in_step_5")
    log.info("Gate 4 loss_cluster: PASS")

    # Step 5 — Position size (Kelly × phase × tier × cluster × DD)
    _append(audit, "5_position_size", fraction=intent.stake_fraction_of_capital)

    caps = default_caps_for_capital(capital)
    if intent.stake_fraction_of_capital > caps.max_fraction_of_capital + 1e-9:
        _append(audit, "5_position_size", fail=True, cap=caps.max_fraction_of_capital)
        log.info("Gate 5 position_cap: FAIL")
        return ChainResult(False, "position_cap", audit, intent)
    log.info("Gate 5 position_size: PASS")

    # Gate 5b — Claude AI (optional; ANTHROPIC_API_KEY + score > 0.25)
    from trading_ai.shark.claude_eval import apply_claude_evaluator_gate

    if HuntType.NEAR_RESOLUTION_HV in intent.hunt_types and float(intent.estimated_win_probability) >= 0.949:
        _append(audit, "5b_claude", ok=True, skipped="hv_high_confidence")
        log.info("HV trade — skipping Claude gate (confidence >= 95%%)")
    else:
        proceed, claude_halt = apply_claude_evaluator_gate(scored, intent, capital=capital)
        if not proceed:
            _append(audit, "5b_claude", decision="SKIP", reason=claude_halt)
            log.info("Gate 5b Claude: FAIL reason=%s", claude_halt)
            return ChainResult(False, claude_halt, audit, intent)
        _append(audit, "5b_claude", ok=True)

    # Step 6 — Fee-adjusted profitability
    if edge > 0 and fee_to_edge_ratio > 0.80:
        _append(audit, "6_fees_kill_edge", ratio=fee_to_edge_ratio)
        log.info("Gate 6 fee: FAIL ratio=%.3f", fee_to_edge_ratio)
        return ChainResult(False, "fee_kill", audit, intent)
    _append(audit, "6_fee_check", ok=True, fee_to_edge_ratio=fee_to_edge_ratio)
    log.info("Gate 6 fee: PASS")

    # Step 7 — Execution delay / latency kill (skipped for Kalshi / Polymarket — no meaningful latency)
    o_low = (outlet or "").strip().lower()
    skip_latency = o_low in ("kalshi", "polymarket")
    erosion = estimated_execution_delay_seconds * 0.001
    if not skip_latency and edge > 0 and erosion / edge > 0.90:
        _append(audit, "7_latency_kill", erosion_ratio=erosion / edge)
        log.info("Gate 7 latency: FAIL erosion_ratio=%.3f", erosion / edge)
        return ChainResult(False, "latency_kill", audit, intent)
    _append(audit, "7_execution_delay", ok=True, seconds=estimated_execution_delay_seconds, skipped=skip_latency)
    log.info("Gate 7 execution_delay: PASS%s", " (skipped for venue)" if skip_latency else "")

    if not getattr(intent, "is_mana", False):
        try:
            if HuntType.NEAR_RESOLUTION_HV in intent.hunt_types:
                from trading_ai.shark.reporting import alert_hv_trade_fired

                alert_hv_trade_fired(scored=scored, intent=intent, capital=capital)
            else:
                alert_trade_fired(
                    hunt_types=[h.value for h in intent.hunt_types],
                    edge=intent.edge_after_fees,
                    position_fraction=intent.stake_fraction_of_capital,
                    capital=capital,
                    tier=str(intent.meta.get("tier", "B")),
                    outlet=intent.outlet,
                    market_desc=str(intent.market_id),
                    resolves_in="TBD",
                    claude_reasoning=intent.meta.get("claude_reasoning"),
                    claude_confidence=intent.meta.get("claude_confidence"),
                )
        except Exception:
            logging.getLogger(__name__).debug("alert_trade_fired failed", exc_info=True)

    # Step 8 — Log submission intent JSONL
    _append(audit, "8_log_intent", market_id=intent.market_id)
    append_shark_audit_record({"step": "intent", "intent": intent.meta | {"market_id": intent.market_id}})

    # Step 8b — Margin safety (hard gate; non-bypassable)
    from trading_ai.shark.margin_control import check_margin_safety, get_margin_allowance, record_margin_position_open

    margin_allowance = get_margin_allowance(
        capital=capital,
        confidence=scored.confidence,
        hunt_tier=scored.tier.value,
        current_drawdown_pct=risk.drawdown_from_peak,
        near_zero_hunt=any(ht == HuntType.NEAR_ZERO_ACCUMULATION for ht in intent.hunt_types),
    )
    if not check_margin_safety(intent.notional_usd, capital, margin_allowance):
        _append(
            audit,
            "8b_margin_gate",
            ok=False,
            notional=intent.notional_usd,
            capital=capital,
            allowance=margin_allowance,
        )
        return ChainResult(False, "margin_unsafe", audit, intent)
    _append(audit, "8b_margin_gate", ok=True, allowance=margin_allowance)

    mb_pre = float(intent.meta.get("margin_borrowed", 0.0))

    if run_live:
        from trading_ai.shark import execution_live as el

        submit_start = time.time()
        try:
            order_res = el.submit_order(intent)
        except Exception as exc:
            logging.getLogger(__name__).exception("submit_order failed")
            _append(audit, "9_submit_order", status="FAILED", error=str(exc))
            append_shark_audit_record({"step": "submit_failed", "market_id": intent.market_id, "error": str(exc)})
            return ChainResult(False, "submit_failed", audit, intent)

        if not getattr(order_res, "success", True):
            st = str(order_res.status or "blocked")
            rs = getattr(order_res, "reason", None) or ""
            _append(audit, "9_submit_order", status=st, reason=rs, order_id=order_res.order_id)
            append_shark_audit_record(
                {"step": "submit_blocked", "market_id": intent.market_id, "status": st, "reason": rs}
            )
            return ChainResult(False, st, audit, intent)

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

        exec_ms = int((time.time() - submit_start) * 1000)
        from trading_ai.shark.trade_journal import log_trade_opened

        tid = log_trade_opened(
            intent,
            order_res,
            conf=conf,
            scored=scored,
            execution_time_ms=max(0, exec_ms),
            dry_run=False,
        )
        if tid:
            intent.meta["trade_id"] = tid
            try:
                from datetime import datetime, timezone

                from trading_ai.automation.telegram_trade_events import maybe_notify_trade_placed

                m = scored.market if scored is not None else None
                if m is not None:
                    q = str(
                        getattr(m, "question_text", None) or m.resolution_criteria or intent.market_id
                    )[:2000]
                    mcat = str(getattr(m, "market_category", None) or "").strip() or None
                else:
                    q = str(intent.market_id)[:2000]
                    mcat = None
                side = str(intent.side or "yes").lower()
                pos_lbl = "YES" if side == "yes" else "NO"
                maybe_notify_trade_placed(
                    None,
                    {
                        "trade_id": tid,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "market": q,
                        "ticker": str(intent.market_id),
                        "position": pos_lbl,
                        "entry_price": float(conf.actual_fill_price),
                        "capital_allocated": float(intent.notional_usd),
                        "market_category": mcat,
                    },
                )
            except Exception:
                logging.getLogger(__name__).warning("post_trade placed notify failed", exc_info=True)

        if not getattr(intent, "is_mana", False) and mb_pre > 1e-9:
            record_margin_position_open(mb_pre)
            try:
                from trading_ai.shark.reporting import send_margin_trade_alert
                from trading_ai.shark.state_store import load_capital as _lc

                rec = _lc()
                send_margin_trade_alert(
                    intent=intent,
                    deposited_capital=rec.starting_capital,
                    confidence=scored.confidence,
                )
            except Exception:
                logging.getLogger(__name__).debug("send_margin_trade_alert failed", exc_info=True)

        pos = el.build_open_position_from_intent(intent, order_res, conf)
        el.monitor_position(pos)
        _append(audit, "11_monitor_resolution", position_id=pos.position_id, poll_seconds=60)
        _append(audit, "12_log_outcome", status="open_position_tracked")
    else:
        from trading_ai.shark.models import ConfirmationResult
        from trading_ai.shark.trade_journal import log_trade_opened

        _stub = ConfirmationResult(
            actual_fill_price=float(intent.expected_price or 0.5),
            actual_fill_size=float(intent.shares or 0),
            slippage_pct=0.0,
            confirmed=True,
        )
        tid = log_trade_opened(
            intent,
            None,
            conf=_stub,
            scored=scored,
            execution_time_ms=0,
            dry_run=True,
        )
        if tid:
            intent.meta["trade_id"] = tid
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
    margin_borrowed_usd: float = 0.0,
    claude_true_probability: Optional[float] = None,
    claude_decision: Optional[str] = None,
    position_side: Optional[str] = None,
    journal_trade_id: Optional[str] = None,
    resolution_outcome: Optional[str] = None,
    journal_pnl_usd: Optional[float] = None,
) -> None:
    from trading_ai.shark.models import HuntType
    from trading_ai.shark.state import LOSS_TRACKER

    if margin_borrowed_usd > 1e-9:
        from trading_ai.shark.margin_control import release_margin_after_close

        release_margin_after_close()

    hts = [h if isinstance(h, HuntType) else HuntType(h) for h in hunt_types]
    trigger_bayesian_after_resolution(
        strategy=strategy, hunt_types=hts, outlet=outlet, win=win, hour_utc=hour_utc
    )
    if claude_decision in ("YES", "NO") and position_side in ("yes", "no"):
        outcome_yes = (position_side == "yes" and win) or (position_side == "no" and not win)
        logging.getLogger(__name__).info(
            "Claude learning: predicted=%s outcome_yes=%s prob=%s",
            claude_decision,
            outcome_yes,
            claude_true_probability,
        )
        BAYES.update_claude_direction_feedback(
            outcome_yes=outcome_yes,
            claude_decision=claude_decision,
        )
        save_bayesian_snapshot()
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

    jid = (journal_trade_id or "").strip() or None
    if not jid and trade_id:
        t = str(trade_id).strip()
        if len(t) == 36 and t.count("-") == 4:
            jid = t
    if jid and resolution_outcome is not None:
        from trading_ai.shark.trade_journal import exit_price_for_binary_side, log_trade_resolved

        ps = (position_side or "yes").lower()
        ex = exit_price_for_binary_side(ps, resolution_outcome)
        jp = journal_pnl_usd if journal_pnl_usd is not None else (pnl_dollars if pnl_dollars is not None else 0.0)
        log_trade_resolved(jid, ex, float(jp), "win" if win else "loss")


def refresh_capital_snapshot_after_external_update() -> None:
    """If capital updated elsewhere, re-save phase line."""
    rec = load_capital()
    save_capital(rec)
