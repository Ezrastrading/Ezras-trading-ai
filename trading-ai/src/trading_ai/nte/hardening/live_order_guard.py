"""Single choke point: no live Coinbase (or avenue) order unless all gates pass."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Literal, Optional, Set

from trading_ai.nte.config.config_validator import validate_nte_settings
from trading_ai.nte.execution.product_rules import validate_order_size
from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure
from trading_ai.nte.hardening.coinbase_product_policy import coinbase_product_nte_allowed
from trading_ai.nte.hardening.mode_context import (
    ExecutionMode,
    describe_coinbase_avenue_enablement,
    get_mode_context,
)
from trading_ai.nte.paths import nte_system_health_path
from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)

# Actions that may proceed when execution is paused (exit / safety).
_EXIT_SAFE_ACTIONS: Set[str] = {
    "place_market_exit",
    "emergency_flat",
}

ALLOWED_ACTIONS: Set[str] = {
    "place_limit_entry",
    "place_market_entry",
    "place_market_exit",
    "cancel_order",
    "retry_order",
    "emergency_flat",
    "replace_order",
}


def _load_system_health() -> Dict[str, Any]:
    p = nte_system_health_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.debug("system_health read failed: %s", exc)
        return {}


def _merge_system_health(updates: Dict[str, Any]) -> None:
    p = nte_system_health_path()
    cur: Dict[str, Any] = {}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cur = raw
        except Exception:
            pass
    cur.update(updates)
    cur["ts"] = time.time()
    atomic_write_json(p, cur)


def _normalize_pem(raw: str) -> str:
    if not raw:
        return raw
    return raw.replace("\\n", "\n").strip()


def _coinbase_credentials_ready() -> bool:
    key = (
        (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "")
        .strip()
    )
    secret = (
        os.environ.get("COINBASE_API_PRIVATE_KEY")
        or os.environ.get("COINBASE_API_SECRET")
        or ""
    ).strip()
    if not key or not secret:
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = _normalize_pem(secret)
        load_pem_private_key(pem.encode("utf-8"), password=None)
        return True
    except Exception:
        return False


def _validate_size_notional(
    action: str,
    *,
    order_side: Optional[str],
    base_size: Optional[str],
    quote_notional: Optional[float],
) -> Optional[str]:
    if action in _EXIT_SAFE_ACTIONS:
        if base_size is not None:
            try:
                if float(str(base_size).replace(",", "")) <= 0:
                    return "invalid_base_size_non_positive"
            except (TypeError, ValueError):
                return "invalid_base_size_parse"
        return None
    if action in ("place_limit_entry", "place_market_entry", "replace_order"):
        if base_size is not None:
            try:
                if float(str(base_size).replace(",", "")) <= 0:
                    return "invalid_base_size_non_positive"
            except (TypeError, ValueError):
                return "invalid_base_size_parse"
        if quote_notional is not None and quote_notional <= 0:
            return "invalid_quote_notional_non_positive"
        if order_side and str(order_side).strip().upper() not in ("BUY", "SELL"):
            return "invalid_order_side"
    return None


def deployment_micro_validation_duplicate_isolation_key() -> Optional[str]:
    """
    When ``python -m trading_ai.deployment micro-validation`` runs a streak, it sets
    ``EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE`` plus session + run index. Each run then gets a
    distinct failsafe duplicate key so successive validation round-trips on the same product do not
    collide with the normal ``PRODUCT:place_market_entry`` duplicate window.

    Returns None outside that controlled context — production duplicate behavior unchanged.
    """
    if (os.environ.get("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None
    sid = (os.environ.get("EZRAS_MICRO_VALIDATION_SESSION_ID") or "").strip()
    run = (os.environ.get("EZRAS_MICRO_VALIDATION_RUN_INDEX") or "").strip()
    if not sid or not run:
        return None
    return f"{sid}_r{run}"


def gate_b_live_micro_validation_duplicate_isolation_key() -> Optional[str]:
    """
    When :func:`run_gate_b_live_micro_validation` runs, it sets
    ``EZRAS_GATE_B_LIVE_MICRO_VALIDATION_ACTIVE`` plus session + run index so successive Gate B
    live-micro round-trips on the same product do not collide with the standard duplicate window
    (and remain distinct from Gate A validation streak keys).
    """
    if (os.environ.get("EZRAS_GATE_B_LIVE_MICRO_VALIDATION_ACTIVE") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None
    sid = (os.environ.get("EZRAS_GATE_B_LIVE_MICRO_SESSION_ID") or "").strip()
    run = (os.environ.get("EZRAS_GATE_B_LIVE_MICRO_RUN_INDEX") or "").strip()
    if not sid or not run:
        return None
    return f"gate_b_lm_{sid}_r{run}"


def validation_duplicate_isolation_key() -> Optional[str]:
    """Deployment streak OR Gate B live-micro session scope (mutually exclusive in practice)."""
    return deployment_micro_validation_duplicate_isolation_key() or gate_b_live_micro_validation_duplicate_isolation_key()


def assert_live_order_permitted(
    action: str,
    avenue_id: str,
    product_id: str,
    strategy_id: Optional[str] = None,
    source: str = "execution",
    *,
    order_side: Optional[str] = None,
    base_size: Optional[str] = None,
    quote_notional: Optional[float] = None,
    skip_config_validation: bool = False,
    credentials_ready: Optional[bool] = None,
    execution_gate: str = "gate_a",
    quote_balances_for_capital_truth: Optional[Dict[str, float]] = None,
    trade_id: Optional[str] = None,
    multi_leg: bool = False,
) -> None:
    """
    Raise RuntimeError if a live order must not be sent.

    Call from the lowest-level order router (e.g. CoinbaseClient place/cancel).
    """
    ctx = get_mode_context()
    health = _load_system_health()
    health_snapshot_id = health.get("snapshot_id") or health.get("health_snapshot_id")

    def _block(reason: str, *, severe: bool = False) -> None:
        meta = {
            "timestamp": time.time(),
            "avenue_id": avenue_id,
            "action": action,
            "product_id": product_id,
            "mode": ctx.execution_mode.value,
            "reason_blocked": reason,
            "health_snapshot_id": health_snapshot_id,
            "strategy_id": strategy_id,
            "source": source,
        }
        if (avenue_id or "").lower() == "coinbase":
            meta["coinbase_avenue_enablement"] = describe_coinbase_avenue_enablement()
        log_failure(
            FailureClass.MODE_MISMATCH,
            f"Live order blocked: {reason}",
            severity="critical" if severe else "warning",
            avenue=avenue_id,
            metadata=meta,
            pause_recommended=severe,
        )
        if severe:
            _merge_system_health(
                {
                    "live_order_guard_blocked": True,
                    "last_block_reason": reason,
                    "healthy": False,
                    "degraded_components": list(
                        set((health.get("degraded_components") or []) + ["live_order_guard"])
                    ),
                }
            )
        raise RuntimeError(f"Live order blocked: {reason}")

    if action not in ALLOWED_ACTIONS:
        _block(f"unknown_action:{action}", severe=False)

    sz_err = _validate_size_notional(
        action,
        order_side=order_side,
        base_size=base_size,
        quote_notional=quote_notional,
    )
    if sz_err:
        _block(sz_err, severe=False)

    # Universal live guard registry (fail-closed) — single eval per assert_live_order_permitted call
    try:
        from trading_ai.safety.universal_live_guard import run_universal_live_guard_precheck

        ulg = run_universal_live_guard_precheck(
            str(avenue_id).lower(),
            execution_gate,
            runtime_root=ezras_runtime_root(),
            trade_id=trade_id,
        )
        _merge_system_health({"universal_live_guard_last": ulg})
    except RuntimeError as exc:
        _merge_system_health(
            {
                "universal_live_guard_last": {
                    "universal_live_guard_evaluated": True,
                    "universal_live_guard_allowed": False,
                    "universal_live_guard_reason_codes": [str(exc)],
                }
            }
        )
        _block(str(exc), severe=False)

    # Final failsafe layer (kill switch, PnL caps, governance, duplicates, capital truth)
    try:
        from trading_ai.safety.failsafe_guard import FailsafeContext, assert_failsafe_or_raise

        g: Literal["gate_a", "gate_b"] = "gate_b" if execution_gate == "gate_b" else "gate_a"
        dup_iso = validation_duplicate_isolation_key()
        assert_failsafe_or_raise(
            FailsafeContext(
                action=action,
                avenue_id=avenue_id,
                product_id=product_id,
                gate=g,
                quote_notional=quote_notional,
                base_size=base_size,
                quote_balances_by_ccy=quote_balances_for_capital_truth,
                strategy_id=strategy_id,
                trade_id=trade_id,
                multi_leg=multi_leg,
                skip_governance=False,
                validation_duplicate_isolation_key=dup_iso,
            ),
            runtime_root=None,
        )
    except RuntimeError as exc:
        _block(str(exc), severe=False)

    # Mode / flags
    if ctx.execution_mode != ExecutionMode.LIVE:
        _block(f"execution_mode_not_live:{ctx.execution_mode.value}", severe=False)
    if ctx.nte_paper_mode:
        _block("nte_paper_mode", severe=False)
    if ctx.nte_dry_run or ctx.dry_run:
        _block("nte_dry_run", severe=False)
    if not ctx.nte_live_trading_enabled:
        _block("nte_live_trading_not_enabled", severe=False)
    if avenue_id.lower() == "coinbase" and not ctx.coinbase_enabled:
        # ctx.coinbase_enabled follows coinbase_avenue_execution_enabled() — COINBASE_EXECUTION_ENABLED OR COINBASE_ENABLED
        _block("coinbase_avenue_disabled", severe=False)
    if ctx.execution_scope in ("sandbox", "research", "paper"):
        if action not in _EXIT_SAFE_ACTIONS:
            _block(f"execution_scope_blocks_live:{ctx.execution_scope}", severe=True)
    if not ctx.strategy_live_ok and strategy_id:
        if action not in _EXIT_SAFE_ACTIONS:
            _block("strategy_live_not_allowed_for_id", severe=False)
    sid = (strategy_id or "").lower()
    if sid.startswith("sandbox") or "sandbox" in sid:
        if action not in _EXIT_SAFE_ACTIONS:
            _block("sandbox_strategy_id", severe=True)

    global_pause = bool(health.get("global_pause"))
    if global_pause and action not in _EXIT_SAFE_ACTIONS:
        _block("global_pause", severe=False)

    avenue_pause = health.get("avenue_pause") or {}
    if isinstance(avenue_pause, dict) and avenue_pause.get(avenue_id):
        if action not in _EXIT_SAFE_ACTIONS:
            _block("avenue_pause", severe=False)

    exec_pause = bool(health.get("execution_should_pause"))
    unhealthy = health.get("healthy") is False
    if (exec_pause or unhealthy) and action not in _EXIT_SAFE_ACTIONS:
        _block("system_health_blocks_execution", severe=False)

    cred_ok = credentials_ready if credentials_ready is not None else _coinbase_credentials_ready()
    if avenue_id.lower() == "coinbase" and not cred_ok:
        _block("coinbase_credentials_missing_or_invalid", severe=True)

    if not coinbase_product_nte_allowed(product_id):
        _block("product_not_allowed", severe=False)

    ok_sz, sz_reason = validate_order_size(
        product_id,
        base_size=base_size,
        quote_notional_usd=quote_notional,
    )
    if not ok_sz:
        _block(f"product_rules:{sz_reason}", severe=False)

    route = (os.environ.get("NTE_COINBASE_EXECUTION_ROUTE") or "live").strip().lower()
    if route not in ("live", "production", "prod"):
        _block(f"execution_route_not_live:{route}", severe=False)

    if not skip_config_validation:
        ok, errs = validate_nte_settings()
        if not ok:
            _block("config_invalid:" + ";".join(errs), severe=True)

    if avenue_id.lower() == "coinbase" and action not in _EXIT_SAFE_ACTIONS:
        try:
            from trading_ai.nte.hardening.control_artifact_preflight import (
                control_artifact_preflight_enabled,
                require_control_artifacts_for_live_execution,
            )

            if control_artifact_preflight_enabled():
                require_control_artifacts_for_live_execution()
        except RuntimeError as exc:
            _block(str(exc), severe=True)
        except Exception as exc:
            logger.debug("control artifact preflight skipped: %s", exc)

    try:
        from trading_ai.runtime.live_execution_state import record_execution_step

        record_execution_step(
            step=f"order_guard_passed:{action}",
            avenue=avenue_id,
            gate=execution_gate,
            mode="executing",
            trade_id=trade_id or "",
            success=True,
            health="healthy",
        )
    except Exception:
        pass


def assert_live_order_permitted_legacy(operation: str) -> None:
    """Backward-compatible single-arg entry used by smoke tests and scripts."""
    assert_live_order_permitted(
        operation,
        avenue_id="coinbase",
        product_id="*",
        strategy_id=None,
        source="legacy",
    )
