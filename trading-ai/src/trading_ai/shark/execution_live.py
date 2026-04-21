"""
Live venue execution — submit, confirm, monitor, resolve.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trading_ai.governance.storage_architecture import append_shark_audit_record
from trading_ai.shark.models import (
    ConfirmationResult,
    ExecutionIntent,
    OpenPosition,
    OrderResult,
)
from trading_ai.shark.risk_context import check_drawdown_after_resolution
from trading_ai.shark.state_store import load_positions, save_positions

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]


def _universal_live_guard_shark_block(intent: ExecutionIntent, *, outlet: str, gate: str) -> Optional[OrderResult]:
    """Non-Coinbase shark outlets: registry + halt (Coinbase uses live_order_guard.assert_live_order_permitted only)."""
    try:
        from trading_ai.safety.universal_live_guard import run_universal_live_guard_precheck

        meta = intent.meta if isinstance(intent.meta, dict) else {}
        tid = str(meta.get("client_order_id") or intent.market_id or "").strip()
        run_universal_live_guard_precheck(str(outlet).lower(), gate, trade_id=tid or None)
    except RuntimeError as exc:
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="universal_live_guard",
            outlet=str(outlet),
            raw={"reason": str(exc)},
            success=False,
            reason=str(exc),
        )
    return None


def _trading_intelligence_block(intent: ExecutionIntent) -> Optional[OrderResult]:
    """Prefer NO_TRADE over BAD_TRADE when ``EZRAS_TRADING_INTELLIGENCE`` is enabled."""
    try:
        from trading_ai.intelligence.preflight import run_intelligence_preflight, trading_intelligence_enabled

        if not trading_intelligence_enabled():
            return None
        meta = intent.meta if isinstance(intent.meta, dict) else {}
        nu = float(getattr(intent, "notional_usd", 0.0) or 0.0)
        if nu <= 0:
            nu = float(meta.get("quote_usd") or meta.get("notional_usd") or meta.get("size_usd") or 0.0)
        allow, reason, ctx = run_intelligence_preflight(
            outlet=str(intent.outlet or ""),
            intent_meta=meta,
            system_ok=True,
            notional_usd=max(nu, 1e-9),
        )
        if allow:
            return None
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="intelligence_blocked",
            outlet=str(intent.outlet or ""),
            raw={"intelligence_context": ctx},
            success=False,
            reason=reason or "intelligence_blocked",
        )
    except Exception as exc:
        logger.debug("trading intelligence preflight skipped: %s", exc)
        return None


def _governance_preflight_live_submit(
    intent: ExecutionIntent,
    *,
    operation: str,
    route: str,
) -> Optional[OrderResult]:
    """
    Returns a failed OrderResult if joint governance blocks; None if caller should proceed.
    Always records ``check_new_order_allowed_full`` (JSON log) before any venue submit.
    """
    from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full

    meta = intent.meta if isinstance(intent.meta, dict) else {}
    tid = str(meta.get("trade_id") or meta.get("journal_trade_id") or "").strip() or None
    ok, reason, _audit = check_new_order_allowed_full(
        venue=str(intent.outlet or "unknown").lower(),
        operation=operation,
        route=route,
        intent_id=tid,
        log_decision=True,
    )
    if ok:
        return None
    return OrderResult(
        order_id="",
        filled_price=0.0,
        filled_size=0.0,
        timestamp=time.time(),
        status="governance_blocked",
        outlet=str(intent.outlet or ""),
        raw={"governance_reason": reason},
        success=False,
        reason=f"governance: {reason}",
    )


def ezras_dry_run_from_env() -> bool:
    """True when ``EZRAS_DRY_RUN`` is set to a truthy value (1, true, yes). Default: false → live execution."""
    v = (os.environ.get("EZRAS_DRY_RUN") or "").strip().lower()
    return v in ("1", "true", "yes")


def manifold_real_money_execution_enabled() -> bool:
    """Manifold is play-money (mana) unless ``MANIFOLD_REAL_MONEY`` is exactly ``true``."""
    return (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower() == "true"


def _core_execution_preflight(intent: ExecutionIntent) -> Optional[OrderResult]:
    """
    Strategy score + capital limits before venue submit (after Polymarket early exits).
    Best-effort: failures in this layer do not block trading.
    """
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    sk = str(meta.get("strategy_key") or "shark_default")
    try:
        from trading_ai.strategy.strategy_validation_engine import get_strategy_validation_engine

        sve = get_strategy_validation_engine()
        if sve.is_strategy_disabled(sk):
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="strategy_blocked",
                outlet=str(intent.outlet or ""),
                raw={"strategy_key": sk},
                success=False,
                reason="strategy_disabled_validation",
            )
    except Exception as exc:
        logger.debug("strategy validation preflight skipped: %s", exc)

    prop = float(getattr(intent, "notional_usd", 0.0) or 0.0)
    if prop <= 0:
        return None
    try:
        from trading_ai.core.capital_engine import capital_preflight_block
        from trading_ai.shark.state_store import load_capital, load_positions

        cap = load_capital()
        bal = float(cap.current_capital or 0.0)
        pos = load_positions()
        exposure = sum(float(p.get("notional_usd") or 0) for p in (pos.get("open_positions") or []))
        blocked, reason = capital_preflight_block(
            proposed_trade_usd=prop,
            account_balance_usd=bal,
            open_exposure_usd=exposure,
            daily_pnl_usd=0.0,
            day_start_balance_usd=bal,
        )
        if blocked:
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="capital_blocked",
                outlet=str(intent.outlet or ""),
                raw={"reason": reason},
                success=False,
                reason=f"capital: {reason}",
            )
    except Exception as exc:
        logger.debug("capital preflight skipped: %s", exc)
    return None


def _hook_intelligence_submit_outcome(intent: ExecutionIntent, result: OrderResult) -> None:
    try:
        from trading_ai.intelligence.integration.live_hooks import record_shark_submit_outcome

        record_shark_submit_outcome(intent, result)
    except Exception:
        logger.debug("intelligence submit outcome hook failed", exc_info=True)


def submit_order(intent: ExecutionIntent) -> OrderResult:
    res = _submit_order_impl(intent)
    _hook_intelligence_submit_outcome(intent, res)
    return res


def _submit_order_impl(intent: ExecutionIntent) -> OrderResult:
    """
    Live submit — credentials:
    Kalshi: ``KALSHI_API_KEY`` (``KalshiClient.place_order``).
    Polymarket: execution blocked by default (US geoblock / ``POLY_EXECUTION_ENABLED``); scanning still uses Polymarket data.
    Manifold: ``MANIFOLD_API_KEY`` (``submit_manifold_bet``).
    """
    o = (intent.outlet or "").lower()
    if o == "polymarket":
        poly_exec = (os.getenv("POLY_EXECUTION_ENABLED") or "false").strip().lower()
        if poly_exec != "true":
            logger.warning("Polymarket execution disabled — intelligence-only (POLY_EXECUTION_ENABLED not true)")
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="polymarket",
                raw={},
                success=False,
                reason="Polymarket execution disabled",
            )
        logger.warning("Polymarket order blocked — US geoblock (scan-only intelligence)")
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="geo_blocked",
            outlet="polymarket",
            raw={},
            success=False,
            reason="US geoblock — scan only",
        )
    blocked_core = _core_execution_preflight(intent)
    if blocked_core is not None:
        return blocked_core
    try:
        from trading_ai.control.kill_switch import kill_switch_active
        from trading_ai.risk.daily_loss_guard import check_daily_loss_limit

        if kill_switch_active():
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="halted",
                outlet=o or "unknown",
                raw={},
                success=False,
                reason="operator_kill_switch",
            )
        dl_b, _ = check_daily_loss_limit()
        if dl_b:
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="halted",
                outlet=o or "unknown",
                raw={},
                success=False,
                reason="daily_loss_limit",
            )
    except Exception:
        logger.debug("operator submit_order preflight skipped", exc_info=True)
    try:
        from trading_ai.shark.production_hardening.runtime_hooks import submit_order_preflight

        pre = submit_order_preflight(intent)
        if pre is not None:
            return pre
    except Exception:
        logger.debug("production_hardening preflight skipped", exc_info=True)
    try:
        from trading_ai.core.system_guard import get_system_guard

        halt_x, why_x = get_system_guard().should_shutdown()
        if halt_x:
            logger.critical("submit_order blocked by system guard: %s", why_x)
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="halted",
                outlet=o or "unknown",
                raw={},
                success=False,
                reason=f"system_guard:{why_x}",
            )
    except Exception:
        logger.debug("system guard submit_order skipped", exc_info=True)
    blocked_intel = _trading_intelligence_block(intent)
    if blocked_intel is not None:
        return blocked_intel
    try:
        from trading_ai.control.system_execution_lock import require_live_execution_allowed

        if o == "kalshi":
            ok_lock, lock_reason = require_live_execution_allowed("gate_b")
        elif o == "coinbase":
            ok_lock, lock_reason = require_live_execution_allowed("gate_a")
        else:
            ok_lock, lock_reason = True, "ok"
        if not ok_lock:
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="system_execution_lock",
                outlet=o or "unknown",
                raw={"reason": lock_reason},
                success=False,
                reason=f"system_execution_lock:{lock_reason}",
            )
    except Exception as exc:
        logger.debug("system_execution_lock preflight skipped: %s", exc)
    if o == "kalshi":
        pre_ulg = _universal_live_guard_shark_block(intent, outlet="kalshi", gate="gate_b")
        if pre_ulg is not None:
            return pre_ulg
        try:
            from trading_ai.shark.coinbase_spot.gate_b_live_status import is_gate_b_live_execution_enabled

            if is_gate_b_live_execution_enabled():
                from trading_ai.control.live_adaptive_integration import (
                    build_live_operating_snapshot,
                    run_live_adaptive_evaluation,
                )

                snap = build_live_operating_snapshot()
                proof = run_live_adaptive_evaluation(
                    snap,
                    write_proof=True,
                    proof_context={
                        "entrypoint": "submit_order",
                        "route": "kalshi_gate_b_live_preflight",
                        "venue": "kalshi",
                        "gate": "gate_b",
                        "trade_intent": "live_order_submit_preflight",
                        "product_id": str(intent.market_id or ""),
                        "proof_source": "trading_ai.shark.execution_live:submit_order",
                    },
                )
                allow = bool(proof.get("allow_new_trades", True))
                mode = str(proof.get("current_operating_mode") or proof.get("mode") or "")
                if not allow or mode == "halted":
                    return OrderResult(
                        order_id="",
                        filled_price=0.0,
                        filled_size=0.0,
                        timestamp=time.time(),
                        status="adaptive_os_blocked",
                        outlet="kalshi",
                        raw={"adaptive_proof": proof},
                        success=False,
                        reason=f"adaptive_os:{mode or 'no_new_trades'}",
                    )
                mult = float(proof.get("size_multiplier_effective") or 1.0)
                if mult > 0.0 and mult < 1.501:
                    meta = dict(intent.meta or {})
                    meta["adaptive_size_multiplier"] = mult
                    meta["adaptive_operating_mode"] = mode
                    intent.meta = meta
                    try:
                        scaled = max(1, int(round(float(intent.shares) * mult)))
                        intent.shares = scaled
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            logger.warning("kalshi_gate_b_adaptive_os_preflight: %s", exc)
        blocked = _governance_preflight_live_submit(
            intent,
            operation="shark_live_submit",
            route=str((intent.meta or {}).get("strategy_key") or "kalshi_default"),
        )
        if blocked is not None:
            return blocked
        from trading_ai.shark.outlets.kalshi import KalshiClient

        client = KalshiClient()
        ticker = intent.market_id
        if ":" in ticker:
            ticker = ticker.split(":")[-1]
        cid = str((intent.meta or {}).get("client_order_id") or "").strip() or None
        res = client.place_order(
            ticker=ticker,
            side=intent.side,
            count=max(1, int(intent.shares)),
            client_order_id=cid,
        )
        try:
            from trading_ai.global_layer.kalshi_execution_mirror import append_kalshi_execution_mirror

            append_kalshi_execution_mirror(
                intent_summary={
                    "outlet": intent.outlet,
                    "market_id": intent.market_id,
                    "side": intent.side,
                    "shares": intent.shares,
                    "strategy_key": (intent.meta or {}).get("strategy_key"),
                },
                order_id=str(res.order_id or ""),
                success=bool(res.success),
                raw_status=str(res.status or ""),
            )
        except Exception as exc:
            logger.warning("kalshi execution mirror append skipped: %s", exc)
        try:
            from trading_ai.runtime.trade_ledger import append_trade_ledger_line
            from trading_ai.runtime_paths import ezras_runtime_root

            lid = str(res.order_id or cid or "").strip() or f"kalshi_{time.time():.3f}"
            append_trade_ledger_line(
                {
                    "trade_id": lid,
                    "avenue_id": "kalshi",
                    "gate_id": "gate_b",
                    "product_id": ticker,
                    "execution_status": str(res.status or "unknown"),
                    "validation_status": "gate_b_non_coinbase_ledger",
                    "failure_reason": None if res.success else str(res.reason or res.status or ""),
                    "capital_truth_hook": "not_applicable_not_coinbase_quote_schema",
                    "ledger_semantics": "gate_b_execution_record_mandatory_stub",
                },
                runtime_root=Path(ezras_runtime_root()),
            )
        except Exception as exc:
            logger.debug("kalshi gate_b ledger append skipped: %s", exc)
        return res
    if o == "manifold":
        pre_m = _universal_live_guard_shark_block(intent, outlet="manifold", gate="default")
        if pre_m is not None:
            return pre_m
        if not manifold_real_money_execution_enabled():
            logger.info("Manifold skipped — play money only")
            raise ValueError("manifold_play_money_skip")
        from trading_ai.shark.manifold_live import submit_manifold_bet

        return submit_manifold_bet(intent)
    if o == "metaculus":
        logger.info("Metaculus is intelligence-only — no orders")
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="intelligence_only",
            outlet="metaculus",
            raw={},
            success=False,
            reason="Metaculus has no tradeable venue in this stack",
        )
    if o == "coinbase":
        from trading_ai.nte.hardening.mode_context import coinbase_avenue_execution_enabled

        if not coinbase_avenue_execution_enabled():
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="coinbase",
                raw={},
                success=False,
                reason="coinbase_live_execution_disabled_set_COINBASE_EXECUTION_ENABLED_or_COINBASE_ENABLED",
            )
        try:
            from trading_ai.organism.deployment_guard import (
                DeploymentGuard,
                assert_system_not_halted,
                deployment_enforcement_enabled,
            )

            if deployment_enforcement_enabled():
                assert_system_not_halted()
                meta = intent.meta if isinstance(intent.meta, dict) else {}
                q = float(getattr(intent, "notional_usd", 0.0) or meta.get("quote_usd") or 0.0)
                px = float(getattr(intent, "expected_price", 0.0) or meta.get("ref_price") or meta.get("mid") or 0.0)
                if q > 0 and px > 0:
                    DeploymentGuard().validate_pre_trade({"quote_size": q, "price": px})
        except RuntimeError:
            raise
        except Exception as exc:
            try:
                from trading_ai.organism.deployment_guard import deployment_enforcement_enabled as _den

                if _den():
                    raise
            except RuntimeError:
                raise
            logger.debug("deployment pre-trade guard skipped: %s", exc)
        blocked = _governance_preflight_live_submit(
            intent,
            operation="shark_live_coinbase_submit",
            route=str((intent.meta or {}).get("strategy_key") or "coinbase_shark"),
        )
        if blocked is not None:
            return blocked
        from trading_ai.shark.outlets.coinbase import CoinbaseFetcher

        pid = str(intent.meta.get("product_id") or intent.market_id or "BTC-USD")
        side = str(intent.side or "buy")
        size = str(intent.meta.get("base_size") or intent.shares or "0")
        if side.lower() == "sell":
            from trading_ai.organism.trade_truth import assert_no_oversell

            try:
                req_base = float(size)
            except (TypeError, ValueError):
                req_base = float(intent.shares or 0)
            pos_base = float(
                intent.meta.get("position_base_size")
                or intent.meta.get("available_base")
                or intent.meta.get("base_size")
                or 0
            )
            if pos_base > 0:
                sell_base = min(pos_base, req_base)
                assert_no_oversell(pos_base, sell_base)
                size = str(sell_base)
        cid_cb = str((intent.meta or {}).get("client_order_id") or "").strip()
        meta_cb = intent.meta if isinstance(intent.meta, dict) else {}
        gex = str(meta_cb.get("execution_gate") or meta_cb.get("gate_id") or "gate_a").strip().lower()
        if "gate_b" in gex or gex in ("b", "gb"):
            egate = "gate_b"
        else:
            egate = "gate_a"
        r = CoinbaseFetcher.place_market_order(
            pid, side, size, client_order_id=cid_cb or None, execution_gate=egate
        )
        return OrderResult(
            order_id=str((r.get("raw") or {}).get("order_id", "") or ""),
            filled_price=float(intent.expected_price or 0.0),
            filled_size=float(intent.shares or 0.0),
            timestamp=time.time(),
            status="submitted" if r.get("ok") else "error",
            outlet="coinbase",
            raw=r,
            success=bool(r.get("ok")),
            reason=None if r.get("ok") else str(r.get("error")),
        )
    if o == "robinhood":
        pre_rh = _universal_live_guard_shark_block(intent, outlet="robinhood", gate="default")
        if pre_rh is not None:
            return pre_rh
        if (os.environ.get("ROBINHOOD_EXECUTION_ENABLED") or "").strip().lower() != "true":
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="robinhood",
                raw={},
                success=False,
                reason="ROBINHOOD_EXECUTION_ENABLED is not true",
            )
        blocked = _governance_preflight_live_submit(
            intent,
            operation="shark_live_robinhood_submit",
            route=str((intent.meta or {}).get("strategy_key") or "robinhood_shark"),
        )
        if blocked is not None:
            return blocked
        from trading_ai.shark.outlets.robinhood import RobinhoodFetcher

        sym = str(intent.market_id or intent.meta.get("symbol") or "").upper()
        sh = float(intent.shares or intent.meta.get("shares") or 0.0)
        rh = RobinhoodFetcher()
        if (intent.side or "buy").lower() == "sell":
            r = rh.sell_market(sym, sh)
        else:
            r = rh.buy_market(sym, sh)
        return OrderResult(
            order_id=str((r.get("raw") or {}).get("id", "") or ""),
            filled_price=float(intent.expected_price or 0.0),
            filled_size=sh,
            timestamp=time.time(),
            status="submitted" if r.get("ok") else "error",
            outlet="robinhood",
            raw=r,
            success=bool(r.get("ok")),
            reason=None if r.get("ok") else str(r.get("error")),
        )
    if o == "tastytrade":
        pre_tt = _universal_live_guard_shark_block(intent, outlet="tastytrade", gate="default")
        if pre_tt is not None:
            return pre_tt
        if (os.environ.get("TASTYTRADE_EXECUTION_ENABLED") or "").strip().lower() != "true":
            return OrderResult(
                order_id="",
                filled_price=0.0,
                filled_size=0.0,
                timestamp=time.time(),
                status="disabled",
                outlet="tastytrade",
                raw={},
                success=False,
                reason="TASTYTRADE_EXECUTION_ENABLED is not true",
            )
        return OrderResult(
            order_id="",
            filled_price=0.0,
            filled_size=0.0,
            timestamp=time.time(),
            status="not_wired",
            outlet="tastytrade",
            raw={},
            success=False,
            reason="Map options legs via TastytradeClient.place_order before enabling",
        )
    raise ValueError(f"unknown outlet: {intent.outlet}")


def confirm_execution(
    order_result: OrderResult,
    intent: ExecutionIntent,
    *,
    sleep_fn: Optional[SleepFn] = None,
    time_fn: Optional[Callable[[], float]] = None,
    poll_order: Optional[Callable[[str], Dict[str, Any]]] = None,
    cancel_order: Optional[Callable[[str], None]] = None,
) -> ConfirmationResult:
    """Verify fill and slippage vs ``expected_price``. Kalshi fills are finalized in ``KalshiClient.place_order``."""
    _ = sleep_fn, time_fn, poll_order, cancel_order  # retained for API compatibility
    exp = max(intent.expected_price, 1e-9)
    fp = order_result.filled_price or exp
    slip = abs(fp - exp) / exp
    edge = max(intent.edge_after_fees, 1e-9)
    high_slip = slip > (0.30 * edge)

    unfilled_cancelled = False
    if intent.outlet.lower() == "kalshi":
        fs_k = float(order_result.filled_size or 0.0)
        st_k = (order_result.status or "").lower()
        if fs_k <= 0.0 or st_k in ("canceled", "cancelled"):
            return ConfirmationResult(
                actual_fill_price=0.0,
                actual_fill_size=0.0,
                slippage_pct=0.0,
                confirmed=False,
                high_slippage_warning=False,
                unfilled_cancelled=True,
            )

    return ConfirmationResult(
        actual_fill_price=fp,
        actual_fill_size=order_result.filled_size,
        slippage_pct=slip,
        confirmed=True,
        high_slippage_warning=high_slip,
        unfilled_cancelled=unfilled_cancelled,
    )


def _default_poll_kalshi(order_id: str) -> Dict[str, Any]:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    return KalshiClient().get_order(order_id)


def _default_cancel_kalshi(order_id: str) -> None:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    KalshiClient().cancel_order(order_id)


def monitor_position(
    position: OpenPosition,
    *,
    save: bool = True,
) -> None:
    data = load_positions()
    ops: List[Dict[str, Any]] = list(data.get("open_positions") or [])
    ops.append(
        {
            "position_id": position.position_id,
            "outlet": position.outlet,
            "market_id": position.market_id,
            "side": position.side,
            "entry_price": position.entry_price,
            "shares": position.shares,
            "notional_usd": position.notional_usd,
            "order_id": position.order_id,
            "opened_at": position.opened_at,
            "strategy_key": position.strategy_key,
            "hunt_types": position.hunt_types,
            "market_category": position.market_category,
            "expected_edge": position.expected_edge,
            "condition_id": position.condition_id,
            "token_id": position.token_id,
            "margin_borrowed_usd": position.margin_borrowed_usd,
            "claude_reasoning": position.claude_reasoning,
            "claude_confidence": position.claude_confidence,
            "claude_true_probability": position.claude_true_probability,
            "claude_decision": position.claude_decision,
            "journal_trade_id": position.journal_trade_id,
        }
    )
    data["open_positions"] = ops
    if save:
        save_positions(data)


def handle_resolution(
    position: OpenPosition,
    outcome: str,
    pnl: float,
    *,
    trade_id: str,
    strategy_key: str,
    hunt_types: list,
    market_category: str,
    hour_utc: Optional[int] = None,
) -> None:
    from datetime import datetime, timezone

    from trading_ai.automation.telegram_trade_events import maybe_notify_trade_closed
    from trading_ai.shark.execution import hook_post_trade_resolution
    from trading_ai.shark.state_store import apply_win_loss_to_capital

    apply_win_loss_to_capital(pnl)

    try:
        from trading_ai.shark.production_hardening.loss_cooldown import record_trade_close_pnl
        from trading_ai.shark.production_hardening.metrics_dashboard import record_trade_metrics

        record_trade_close_pnl(float(pnl))
        record_trade_metrics(
            venue=str(position.outlet or ""),
            realized_pnl_delta=float(pnl),
            gross_pnl=abs(float(pnl)),
            fees_usd=0.0,
        )
    except Exception:
        logger.debug("production_hardening resolution metrics skipped", exc_info=True)

    try:
        from trading_ai.core.portfolio_engine import PortfolioEngine
        from trading_ai.core.system_guard import get_system_guard

        get_system_guard().record_closed_trade_pnl(float(pnl))
        PortfolioEngine().record_realized_pnl(str(position.outlet or "unknown").strip().lower(), float(pnl))
    except Exception:
        logger.debug("system_guard/portfolio resolution hook skipped", exc_info=True)

    data = load_positions()
    ops = [p for p in (data.get("open_positions") or []) if p.get("position_id") != position.position_id]
    data["open_positions"] = ops
    hist = list(data.get("history") or [])
    hunt_vals = [getattr(h, "value", str(h)) for h in (hunt_types or [])]
    hist.append(
        {
            "position_id": position.position_id,
            "outlet": position.outlet,
            "market_id": position.market_id,
            "outcome": outcome,
            "pnl": pnl,
            "closed_at": time.time(),
            "hunt_types": hunt_vals,
            "market_category": market_category,
        }
    )
    data["history"] = hist
    save_positions(data)

    win = pnl > 0
    hook_post_trade_resolution(
        trade_id,
        win=win,
        strategy=strategy_key,
        hunt_types=hunt_types,
        outlet=position.outlet,
        market_id=position.market_id,
        market_category=market_category,
        hour_utc=hour_utc,
        pnl_dollars=None,
        update_capital=False,
        margin_borrowed_usd=position.margin_borrowed_usd,
        claude_true_probability=position.claude_true_probability,
        claude_decision=position.claude_decision,
        position_side=position.side,
        journal_trade_id=position.journal_trade_id,
        resolution_outcome=outcome,
        journal_pnl_usd=pnl,
    )

    append_shark_audit_record(
        {
            "event": "resolution",
            "trade_id": trade_id,
            "position_id": position.position_id,
            "pnl": pnl,
            "outcome": outcome,
        }
    )

    jid = (position.journal_trade_id or "").strip() or str(trade_id).strip()
    notional = float(position.notional_usd or 0.0)
    roi = (pnl / notional * 100.0) if notional > 1e-9 else 0.0
    ps = str(position.side or "yes").lower()
    pos_lbl = "YES" if ps == "yes" else "NO"
    payout_dollars = notional + pnl
    tick = str(position.market_id or "").strip()
    if ":" in tick:
        tick = tick.split(":")[-1]
    try:
        maybe_notify_trade_closed(
            None,
            {
                "trade_id": jid,
                "result": "win" if win else "loss",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market": f"{position.outlet}: {tick}",
                "ticker": tick,
                "position": pos_lbl,
                "gross_pnl_dollars": pnl,
                "net_pnl_dollars": pnl,
                "capital_allocated": notional,
                "roi_percent": roi,
                "payout_dollars": payout_dollars,
            },
        )
    except Exception:
        logger.exception("post_trade closed notify failed")

    check_drawdown_after_resolution()


def build_open_position_from_intent(
    intent: ExecutionIntent,
    order: OrderResult,
    conf: ConfirmationResult,
) -> OpenPosition:
    return OpenPosition(
        position_id=str(uuid.uuid4()),
        outlet=intent.outlet,
        market_id=intent.market_id,
        side=intent.side,
        entry_price=conf.actual_fill_price,
        shares=conf.actual_fill_size,
        notional_usd=intent.notional_usd,
        order_id=order.order_id,
        opened_at=time.time(),
        strategy_key="shark_default",
        hunt_types=[h.value for h in intent.hunt_types],
        market_category=intent.meta.get("market_category", "default"),
        expected_edge=intent.edge_after_fees,
        condition_id=intent.meta.get("condition_id"),
        token_id=intent.meta.get("token_id"),
        margin_borrowed_usd=float(intent.meta.get("margin_borrowed", 0.0)),
        claude_reasoning=intent.meta.get("claude_reasoning"),
        claude_confidence=intent.meta.get("claude_confidence"),
        claude_true_probability=intent.meta.get("claude_true_probability"),
        claude_decision=intent.meta.get("claude_decision"),
        journal_trade_id=str(intent.meta.get("trade_id") or "") or None,
    )


def calculate_pnl(position: OpenPosition, outcome: str) -> float:
    """Binary contract P&L from resolution outcome string."""
    o = (outcome or "").strip().upper()
    yes_win = o in ("YES", "Y", "TRUE", "1", "WIN")
    no_win = o in ("NO", "N", "FALSE", "0", "LOSS")
    px = max(position.entry_price, 1e-9)
    stake = position.notional_usd
    if position.side.lower() == "yes":
        if yes_win:
            return stake * (1.0 / px - 1.0)
        if no_win:
            return -stake
    else:
        p_no = max(1.0 - px, 1e-9)
        if no_win:
            return stake * (1.0 / p_no - 1.0)
        if yes_win:
            return -stake
    return 0.0


def poll_resolution_for_outlet(outlet: str, market_id: str, position: OpenPosition) -> Optional[str]:
    """Return outcome string if resolved, else None."""
    o = outlet.lower()
    if o == "polymarket":
        return _poll_poly(market_id)
    if o == "kalshi":
        return _poll_kalshi(market_id)
    if o == "manifold":
        return _poll_manifold(market_id)
    return None


def _poll_poly(condition_id: str) -> Optional[str]:
    cid = condition_id.replace("poly:", "")
    url = f"https://clob.polymarket.com/markets/{urllib.parse.quote(cid, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        m = j if isinstance(j, dict) else {}
        if m.get("resolved") or m.get("closed"):
            return str(m.get("winner") or m.get("outcome") or "yes")
    except Exception:
        pass
    return None


def _poll_kalshi(ticker: str) -> Optional[str]:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    t = ticker.split(":")[-1]
    try:
        j = KalshiClient().get_market(t)
        st = str(j.get("status") or "").lower()
        if st == "finalized":
            return str(j.get("result") or "yes")
    except Exception:
        pass
    return None


def _poll_manifold(contract_id: str) -> Optional[str]:
    cid = contract_id.replace("manifold:", "")
    url = f"https://api.manifold.markets/v0/market/{urllib.parse.quote(cid, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("isResolved"):
            return str(j.get("resolution") or "YES")
    except Exception:
        pass
    return None
