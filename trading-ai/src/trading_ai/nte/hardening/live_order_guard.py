"""Single choke point: no live Coinbase (or avenue) order unless all gates pass."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Set

from trading_ai.nte.config.config_validator import validate_nte_settings
from trading_ai.nte.execution.product_rules import validate_order_size
from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure
from trading_ai.nte.hardening.mode_context import ExecutionMode, get_mode_context
from trading_ai.nte.paths import nte_system_health_path
from trading_ai.nte.utils.atomic_json import atomic_write_json

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


def _product_allowed(product_id: str) -> bool:
    from trading_ai.nte.config.settings import load_nte_settings

    s = load_nte_settings()
    pid = (product_id or "").strip()
    if pid == "*" or not pid:
        return True
    return pid in set(s.products)


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

    if not _product_allowed(product_id):
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


def assert_live_order_permitted_legacy(operation: str) -> None:
    """Backward-compatible single-arg entry used by smoke tests and scripts."""
    assert_live_order_permitted(
        operation,
        avenue_id="coinbase",
        product_id="*",
        strategy_id=None,
        source="legacy",
    )
