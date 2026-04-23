"""Final failsafe: kill switch, PnL caps, governance, duplicates, streaks."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from trading_ai.control.system_execution_lock import require_live_execution_allowed
from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.nte.execution.product_rules import validate_order_size, venue_min_notional_usd
from trading_ai.nte.hardening.coinbase_product_policy import coinbase_product_nte_allowed
from trading_ai.nte.paths import nte_system_health_path
from trading_ai.runtime.capital_truth import assert_executable_capital_for_product
from trading_ai.runtime.live_execution_state import record_execution_step
from trading_ai.runtime.trade_ledger import iter_ledger_lines, today_utc
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.safety.error_taxonomy import ExecutionErrorCode, normalize_error_code
from trading_ai.safety.duplicate_trade_window import (
    merge_resolution_into_failsafe_state,
    persisted_seconds_for_duplicate_check,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter

logger = logging.getLogger(__name__)

_EXIT_ACTIONS = frozenset(
    {
        "place_market_exit",
        "emergency_flat",
        "cancel_order",
    }
)


def _kill_switch_path() -> str:
    return "data/control/system_kill_switch.json"


def _failsafe_status_path() -> str:
    return "data/control/failsafe_status.json"


def _adapter(rt: Optional[Path]) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=rt)


def load_kill_switch(*, runtime_root: Optional[Path] = None) -> bool:
    ad = _adapter(runtime_root)
    raw = ad.read_json(_kill_switch_path())
    if not raw:
        return False
    return bool(raw.get("active") or raw.get("enabled") or raw.get("kill"))


def write_kill_switch(active: bool, *, note: str = "", runtime_root: Optional[Path] = None) -> None:
    ad = _adapter(runtime_root)
    ad.write_json(
        _kill_switch_path(),
        {
            "active": bool(active),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        },
    )


def default_failsafe_state() -> Dict[str, Any]:
    now = time.time()
    base = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "halted": False,
        "halt_reason": None,
        "daily_pnl_usd": 0.0,
        "session_pnl_usd": 0.0,
        "session_day_utc": today_utc(),
        "session_started_ts": now,
        "fail_streak": 0,
        "recent_orders": [],
        "max_daily_loss_usd": float(os.environ.get("EZRAS_FAILSAFE_MAX_DAILY_LOSS_USD") or "0"),
        "max_session_loss_usd": float(os.environ.get("EZRAS_FAILSAFE_MAX_SESSION_LOSS_USD") or "0"),
        "max_positions": int(os.environ.get("EZRAS_FAILSAFE_MAX_OPEN_POSITIONS") or "0"),
        "max_fail_streak": int(os.environ.get("EZRAS_FAILSAFE_MAX_CONSECUTIVE_FAILURES") or "5"),
    }
    merge_resolution_into_failsafe_state(base)
    return base


def load_failsafe_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ad = _adapter(runtime_root)
    raw = ad.read_json(_failsafe_status_path())
    base = default_failsafe_state()
    if raw:
        base.update(raw)
    # Day rollover
    d = today_utc()
    if str(base.get("session_day_utc") or "") != d:
        base["session_day_utc"] = d
        base["daily_pnl_usd"] = 0.0
    merge_resolution_into_failsafe_state(base)
    return base


def write_failsafe_state(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    ad = _adapter(runtime_root)
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    ad.write_json(_failsafe_status_path(), payload)


def refresh_pnl_from_ledger(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Best-effort rolling PnL from ledger lines for today (closed trades with pnl set)."""
    st = load_failsafe_state(runtime_root=runtime_root)
    day = today_utc()
    daily = 0.0
    for row in iter_ledger_lines(runtime_root=runtime_root, max_lines=5000):
        ts = str(row.get("timestamp_close") or row.get("timestamp_open") or "")
        if day not in ts and not ts.startswith(day):
            continue
        try:
            p = row.get("pnl")
            if p is None:
                continue
            daily += float(p)
        except (TypeError, ValueError):
            continue
    st["daily_pnl_usd"] = daily
    write_failsafe_state(st, runtime_root=runtime_root)
    return st


def _open_positions_estimate(*, runtime_root: Optional[Path] = None) -> int:
    try:
        from trading_ai.shark.state_store import load_positions

        pos = load_positions()
        return len(pos.get("open_positions") or [])
    except Exception:
        return 0


@dataclass
class FailsafeContext:
    action: str
    avenue_id: str
    product_id: str
    gate: Literal["gate_a", "gate_b"]
    quote_notional: Optional[float]
    base_size: Optional[str]
    quote_balances_by_ccy: Optional[Dict[str, float]]
    strategy_id: Optional[str]
    trade_id: Optional[str]
    multi_leg: bool
    skip_governance: bool
    skip_duplicate_guard: bool = False
    #: When set (e.g. deployment micro-validation streak), duplicate key is namespaced — ordinary
    #: ``PRODUCT:action`` duplicate window still applies to live trading without this scope.
    validation_duplicate_isolation_key: Optional[str] = None


def run_failsafe_checks(
    ctx: FailsafeContext,
    *,
    runtime_root: Optional[Path] = None,
) -> Tuple[bool, str, str]:
    """
    Returns (ok, error_code, message). Does not raise — caller maps to RuntimeError for order guard.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    rt = root

    try:
        from trading_ai.safety.kill_switch_engine import evaluate_execution_block

        blocked, halt_reason = evaluate_execution_block(runtime_root=rt)
        if blocked:
            return False, ExecutionErrorCode.SYSTEM_KILL_SWITCH_ACTIVE.value, halt_reason
    except Exception:
        logger.debug("kill_switch_engine evaluate skipped", exc_info=True)

    if load_kill_switch(runtime_root=rt):
        return False, ExecutionErrorCode.SYSTEM_KILL_SWITCH_ACTIVE.value, "halt_active_reason:SYSTEM_KILL_SWITCH_JSON_ACTIVE"

    st = load_failsafe_state(runtime_root=rt)
    if st.get("halted"):
        return False, ExecutionErrorCode.FAILSAFE_HALTED.value, str(st.get("halt_reason") or "halted")

    fs = int(st.get("fail_streak") or 0)
    max_fs = int(st.get("max_fail_streak") or 5)
    if max_fs > 0 and fs >= max_fs:
        return False, ExecutionErrorCode.FAILED_STREAK_HALT.value, "failed streak halt"

    # System health blocked
    try:
        hp = nte_system_health_path()
        if hp.is_file():
            health = json.loads(hp.read_text(encoding="utf-8"))
            if isinstance(health, dict):
                if health.get("global_pause") and ctx.action not in _EXIT_ACTIONS:
                    return False, ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value, "global_pause"
                if health.get("healthy") is False and ctx.action not in _EXIT_ACTIONS:
                    return False, ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value, "system_health_unhealthy"
    except Exception:
        pass

    # Gate / execution lock (capital truth file)
    allowed, rreason = require_live_execution_allowed(ctx.gate, runtime_root=rt)
    if not allowed and ctx.action not in _EXIT_ACTIONS:
        return False, ExecutionErrorCode.GATE_OR_LOCK_BLOCKED.value, rreason

    # Product policy
    pid = (ctx.product_id or "").strip()
    if pid and pid != "*" and not coinbase_product_nte_allowed(pid):
        return False, ExecutionErrorCode.RUNTIME_POLICY_DISALLOWS_FUNDABLE_PRODUCT.value, "product not allowed"

    # Size vs venue min
    if pid and pid != "*" and ctx.action not in _EXIT_ACTIONS:
        ok_sz, sz_reason = validate_order_size(
            pid,
            base_size=ctx.base_size,
            quote_notional_usd=ctx.quote_notional,
        )
        if not ok_sz:
            code = (
                ExecutionErrorCode.VENUE_MIN_NOTIONAL_NOT_FUNDABLE.value
                if sz_reason and "min" in str(sz_reason).lower()
                else ExecutionErrorCode.UNKNOWN_EXECUTION_FAILURE.value
            )
            return False, code, str(sz_reason or "size")

    # Capital truth (entries with quote)
    if (
        ctx.avenue_id.lower() == "coinbase"
        and ctx.quote_notional is not None
        and ctx.action in ("place_market_entry", "place_limit_entry", "replace_order")
        and pid and pid != "*"
    ):
        bal = ctx.quote_balances_by_ccy
        strict = (os.environ.get("EZRAS_CAPITAL_TRUTH_REQUIRED") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if bal is not None:
            ok_ct, reason_ct = assert_executable_capital_for_product(
                pid,
                requested_quote=float(ctx.quote_notional),
                quote_balances_by_ccy=bal,
                multi_leg=ctx.multi_leg,
            )
            if not ok_ct:
                return False, reason_ct, "capital_truth"
        elif strict:
            return False, ExecutionErrorCode.INSUFFICIENT_ALLOWED_QUOTE_BALANCE.value, "capital_truth_required_but_no_balances"

    if ctx.multi_leg:
        return False, ExecutionErrorCode.MULTI_LEG_EXECUTION_NOT_ENABLED.value, "multi_leg_execution_not_enabled"

    # PnL brakes
    refresh_pnl_from_ledger(runtime_root=rt)
    st = load_failsafe_state(runtime_root=rt)
    max_d = float(st.get("max_daily_loss_usd") or 0.0)
    max_s = float(st.get("max_session_loss_usd") or 0.0)
    if max_d > 0 and float(st.get("daily_pnl_usd") or 0.0) <= -max_d:
        return False, ExecutionErrorCode.MAX_DAILY_LOSS_EXCEEDED.value, "max daily loss"
    if max_s > 0 and float(st.get("session_pnl_usd") or 0.0) <= -max_s:
        return False, ExecutionErrorCode.MAX_SESSION_LOSS_EXCEEDED.value, "max session loss"

    # Position cap
    max_pos = int(st.get("max_positions") or 0)
    if max_pos > 0 and ctx.action not in _EXIT_ACTIONS:
        nopen = _open_positions_estimate(runtime_root=rt)
        if nopen >= max_pos:
            return False, ExecutionErrorCode.MAX_POSITION_LIMIT_EXCEEDED.value, "max open positions"

    # Governance (entries)
    if not ctx.skip_governance and ctx.action not in _EXIT_ACTIONS:
        ok_g, reason_g, _ = check_new_order_allowed_full(
            venue=ctx.avenue_id.lower(),
            operation=ctx.action,
            route="failsafe",
            intent_id=ctx.trade_id,
            log_decision=True,
        )
        if not ok_g:
            return False, ExecutionErrorCode.GOVERNANCE_BLOCKED.value, reason_g

    # Duplicate guard (reload state; canonical window from env + persisted — never treat 0 as falsy unset)
    # Never block exits/safety actions (must always remain executable).
    if not ctx.skip_duplicate_guard and ctx.action not in _EXIT_ACTIONS:
        st = load_failsafe_state(runtime_root=rt)
        win, _dwres = persisted_seconds_for_duplicate_check(dict(st), environ=dict(os.environ))
        now = time.time()
        recent = list(st.get("recent_orders") or [])
        recent = [x for x in recent if now - float(x.get("ts") or 0) < 3600.0]
        # Namespace by gate so Gate A and Gate B do not share the same duplicate window for the
        # same product/action (distinct live paths, distinct operator intent).
        key = f"{pid.upper()}:{ctx.action}:{ctx.gate}"
        iso = getattr(ctx, "validation_duplicate_isolation_key", None)
        if iso:
            key = f"{key}:valscope:{iso}"
        if win is not None:
            for row in recent:
                if str(row.get("key")) == key and (now - float(row.get("ts") or 0)) < float(win):
                    return False, ExecutionErrorCode.DUPLICATE_TRADE_GUARD.value, "duplicate_trade_window"
        recent.append({"key": key, "ts": now, "trade_id": ctx.trade_id})
        st["recent_orders"] = recent[-200:]
        merge_resolution_into_failsafe_state(st)
        write_failsafe_state(st, runtime_root=rt)

    return True, "ok", "ok"


def peek_duplicate_trade_window_would_block_entry(
    *,
    product_id: str,
    action: str,
    gate: Literal["gate_a", "gate_b"],
    runtime_root: Optional[Path] = None,
    validation_duplicate_isolation_key: Optional[str] = None,
) -> bool:
    """
    Read-only duplicate-window probe — same key and window math as the duplicate section of
    :func:`run_failsafe_checks` (``PRODUCT:ACTION:GATE`` + optional ``valscope``), without mutating
    failsafe state. Use for supervised preflight so expected skips do not hit order placement.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    st = load_failsafe_state(runtime_root=root)
    win, _dwres = persisted_seconds_for_duplicate_check(dict(st), environ=dict(os.environ))
    now = time.time()
    recent = list(st.get("recent_orders") or [])
    recent = [x for x in recent if now - float(x.get("ts") or 0) < 3600.0]
    pid = (product_id or "").strip()
    key = f"{pid.upper()}:{action}:{gate}"
    iso = validation_duplicate_isolation_key
    if iso:
        key = f"{key}:valscope:{iso}"
    if win is None:
        return False
    for row in recent:
        if str(row.get("key")) == key and (now - float(row.get("ts") or 0)) < float(win):
            return True
    return False


def record_venue_outcome(
    *,
    success: bool,
    product_id: str,
    avenue: str = "coinbase",
    runtime_root: Optional[Path] = None,
    venue_reached: bool = True,
) -> None:
    """Only increments fail_streak on venue-side failures (venue_reached=True). Pre-venue blocks must pass venue_reached=False."""
    if not venue_reached:
        return
    st = load_failsafe_state(runtime_root=runtime_root)
    if success:
        st["fail_streak"] = 0
    else:
        st["fail_streak"] = int(st.get("fail_streak") or 0) + 1
    max_fs = int(st.get("max_fail_streak") or 5)
    fs_now = int(st.get("fail_streak") or 0)
    if max_fs > 0 and fs_now >= max_fs:
        st["halted"] = True
        st["halt_reason"] = "failed_streak_guard"
        record_execution_step(
            step="failsafe_halt_failed_streak",
            avenue=avenue,
            mode="halted",
            success=False,
            error=ExecutionErrorCode.FAILED_STREAK_HALT.value,
            health="halted",
            runtime_root=runtime_root,
        )
        if fs_now == max_fs:
            try:
                from trading_ai.safety.kill_switch_engine import activate_halt

                activate_halt(
                    "REPEATED_EXECUTION_FAILURE_LOOP",
                    source_component="failsafe_guard",
                    severity="CRITICAL",
                    immediate_action_required="Align failsafe streak halt with canonical kill-switch audit trail.",
                    detail={"fail_streak": fs_now, "max_fail_streak": max_fs},
                    runtime_root=runtime_root,
                    rehearsal_mode=False,
                )
            except Exception:
                logger.debug("kill_switch_engine activate from streak failed", exc_info=True)
    write_failsafe_state(st, runtime_root=runtime_root)


def assert_failsafe_or_raise(ctx: FailsafeContext, *, runtime_root: Optional[Path] = None) -> None:
    ok, code, msg = run_failsafe_checks(ctx, runtime_root=runtime_root)
    if ok:
        return
    ncode = normalize_error_code(code)
    raise RuntimeError(f"failsafe_blocked:{ncode}:{msg}")


def update_session_pnl_delta(delta: float, *, runtime_root: Optional[Path] = None) -> None:
    st = load_failsafe_state(runtime_root=runtime_root)
    st["session_pnl_usd"] = float(st.get("session_pnl_usd") or 0.0) + float(delta)
    write_failsafe_state(st, runtime_root=runtime_root)
