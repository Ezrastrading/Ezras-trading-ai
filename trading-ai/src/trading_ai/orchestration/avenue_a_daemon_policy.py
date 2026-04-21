"""Avenue A daemon policy booleans — shared by daemon runner and artifact writers (no I/O cycles)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from trading_ai.orchestration.runtime_runner import (
    evaluate_continuous_daemon_runtime_proven,
    live_execution_gate_ok,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_DAEMON_MODE_ENV = "EZRAS_AVENUE_A_DAEMON_MODE"
_MIN_CONSEC_ENV = "EZRAS_AUTONOMOUS_MIN_CONSECUTIVE_OK_CYCLES"
_STATE_REL = "data/control/avenue_a_daemon_state.json"

# Section 5 — do not collapse armed_off vs enabled; legacy ``autonomous_live`` resolves at execution time.
_DAEMON_MODES = frozenset(
    {
        "disabled",
        "tick_only",
        "paper_execution",
        "supervised_live",
        "autonomous_live",
        "autonomous_live_armed_off",
        "autonomous_live_enabled",
    }
)


def avenue_a_daemon_mode() -> str:
    raw = (os.environ.get(_DAEMON_MODE_ENV) or "disabled").strip().lower()
    if raw in _DAEMON_MODES:
        return raw
    return "disabled"


def avenue_a_is_autonomous_family(mode: str) -> bool:
    return mode in (
        "autonomous_live",
        "autonomous_live_armed_off",
        "autonomous_live_enabled",
    )


def avenue_a_effective_autonomous_execution_tier(*, runtime_root: Path) -> str:
    """
    ``armed_off`` | ``live_enabled`` | ``not_autonomous``

    ``autonomous_live_enabled`` without artifact+env still resolves to ``armed_off`` (honest downgrade).
    """
    mode = avenue_a_daemon_mode()
    if not avenue_a_is_autonomous_family(mode):
        return "not_autonomous"
    if mode == "autonomous_live_armed_off":
        return "armed_off"
    from trading_ai.orchestration.autonomous_daemon_live_contract import autonomous_daemon_may_submit_live_orders

    ok, _ = autonomous_daemon_may_submit_live_orders(runtime_root=runtime_root)
    if mode == "autonomous_live_enabled":
        return "live_enabled" if ok else "armed_off"
    # legacy autonomous_live
    return "live_enabled" if ok else "armed_off"


def min_consecutive_autonomous_cycles_required() -> int:
    try:
        return max(1, int((os.environ.get(_MIN_CONSEC_ENV) or "5").strip() or "5"))
    except ValueError:
        return 5


def avenue_a_supervised_inputs_ok(
    *,
    runtime_root: Path,
    require_daemon_truth: bool = True,
) -> Tuple[bool, str]:
    """Operator + switch + optional daemon authority; plus first_20 when env requires."""
    ok, bl = live_execution_gate_ok(
        runtime_root=runtime_root,
        daemon_live_tier="supervised",
        require_daemon_truth=require_daemon_truth,
    )
    if not ok:
        return False, ";".join(bl)
    require = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in ("1", "true", "yes")
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    f20 = ad.read_json("data/control/first_20_pass_decision.json") or {}
    if require and not bool(f20.get("passed")):
        return False, "EZRAS_FIRST_20_REQUIRED_FOR_LIVE_and_first_20_pass_false"
    return True, "ok"


def avenue_a_supervised_runtime_allowed(*, runtime_root: Path) -> Tuple[bool, str]:
    return avenue_a_supervised_inputs_ok(runtime_root=runtime_root, require_daemon_truth=True)


def avenue_a_autonomous_live_allowed(*, runtime_root: Path) -> Tuple[bool, str]:
    """Daemon-grade autonomous: env/switch/authority + first_20 + autonomous runtime proof."""
    ok, bl = live_execution_gate_ok(
        runtime_root=runtime_root,
        daemon_live_tier="autonomous",
        require_daemon_truth=True,
    )
    if not ok:
        # Return first blocker only - atomic blockers should be processed individually upstream
        return False, bl[0] if bl else "live_execution_gate_not_ok"
    require = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in ("1", "true", "yes")
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    f20 = ad.read_json("data/control/first_20_pass_decision.json") or {}
    if require and not bool(f20.get("passed")):
        return False, "EZRAS_FIRST_20_REQUIRED_FOR_LIVE_and_first_20_pass_false"
    aut_ok, aut_why = avenue_a_autonomous_runtime_proven(runtime_root=runtime_root)
    if not aut_ok:
        return False, aut_why
    return True, "ok"


def avenue_a_autonomous_runtime_proven(*, runtime_root: Path) -> Tuple[bool, str]:
    from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import compute_autonomous_live_runtime_proven_tuple

    ok, blockers = compute_autonomous_live_runtime_proven_tuple(runtime_root=runtime_root)
    if ok:
        return True, "ok"
    # Return first blocker only - atomic blockers should be processed individually upstream
    return False, blockers[0] if blockers else "autonomous_runtime_not_proven"
