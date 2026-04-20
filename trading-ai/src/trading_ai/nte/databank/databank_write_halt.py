"""
Canonical databank (Supabase trade_events) write streak → kill-switch halt.

Emits ``data/control/databank_write_halt_truth.json`` with streak accounting.
Does not replace :mod:`trading_ai.core.system_guard` Supabase diagnostics — complements them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.safety.kill_switch_engine import activate_halt, current_halt_state, load_trigger_registry

logger = logging.getLogger(__name__)

_REASON = "SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD"


def databank_write_halt_state_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return root / "data" / "control" / "databank_write_halt_state.json"


def databank_write_halt_truth_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return root / "data" / "control" / "databank_write_halt_truth.json"


def _default_state() -> Dict[str, Any]:
    return {
        "truth_version": "databank_write_halt_state_v1",
        "consecutive_write_failures": 0,
        "last_failure_reason": None,
        "last_success_unix": None,
        "last_success_iso": None,
        "halt_fired_from_databank_layer": False,
        "last_halt_event_id": None,
        "threshold_source": "env:SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD",
    }


def _read_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base = _default_state()
            base.update(raw)
            return base
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("databank_write_halt state load failed: %s", exc)
    return _default_state()


def _write_state(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def configured_write_failure_threshold() -> int:
    raw = (os.environ.get("SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD") or "").strip()
    if not raw:
        return 3
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _write_truth(runtime_root: Path, truth: Dict[str, Any]) -> None:
    p = databank_write_halt_truth_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_databank_trade_write_outcome(
    success: bool,
    error: Optional[str],
    *,
    runtime_root: Optional[Path] = None,
    rehearsal_mode: bool = False,
    component: str = "supabase_trade_sync.upsert_trade_event",
) -> Dict[str, Any]:
    """
    Call after a logical trade_events upsert attempt completes (success or exhausted retries).

    On ``success``, resets failure streak. On failure, increments streak and may activate halt at threshold.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    st_path = databank_write_halt_state_path(runtime_root=root)
    st = _read_state(st_path)
    threshold = configured_write_failure_threshold()
    reg = load_trigger_registry()
    trig = (reg.get("triggers") or {}).get(_REASON) or {}
    ia = str(trig.get("immediate_action_required") or "Halt; restore databank health; verify sync probes before resume.")

    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    if success:
        st["consecutive_write_failures"] = 0
        st["last_failure_reason"] = None
        st["last_success_unix"] = now
        st["last_success_iso"] = now_iso
        st["halt_fired_from_databank_layer"] = False
        _write_state(st_path, st)
        try:
            from trading_ai.core.system_guard import get_system_guard

            get_system_guard(runtime_root=root).record_supabase_ok()
        except Exception:
            logger.debug("system_guard record_supabase_ok skipped", exc_info=True)

        truth = {
            "truth_version": "databank_write_halt_truth_v1",
            "runtime_root": str(root),
            "threshold_configured": threshold,
            "threshold_source": st.get("threshold_source"),
            "current_failure_streak": 0,
            "halt_fired_from_databank_layer": bool(st.get("halt_fired_from_databank_layer")),
            "firing_component": None,
            "last_failure_reason": None,
            "last_success_timestamp": st.get("last_success_iso"),
            "recovery_eligible": True,
            "recovery_condition": (
                "Consecutive databank write failures reset to zero after a confirmed successful trade_events upsert; "
                "operator may still need to clear kill_switch_truth / recovery_engine per policy."
            ),
            "honesty": "Streak resets only on successful write path — not on queued-local-only durability.",
        }
        _write_truth(root, truth)
        return {"ok": True, "streak": 0, "halt_activated": False, "truth": truth}

    prev = int(st.get("consecutive_write_failures") or 0)
    streak = prev + 1
    st["consecutive_write_failures"] = streak
    st["last_failure_reason"] = (error or "unknown")[:2000]
    _write_state(st_path, st)

    try:
        from trading_ai.core.system_guard import get_system_guard

        get_system_guard(runtime_root=root).record_supabase_failure()
    except Exception:
        logger.debug("system_guard record_supabase_failure skipped", exc_info=True)

    halted_already = bool(current_halt_state(runtime_root=root).get("halted"))
    halt_activated = False
    halt_out: Optional[Dict[str, Any]] = None

    if streak >= threshold and not halted_already and not rehearsal_mode:
        halt_out = activate_halt(
            _REASON,
            source_component=component,
            severity="CRITICAL",
            immediate_action_required=ia,
            detail={
                "consecutive_write_failures": streak,
                "threshold": threshold,
                "last_failure_reason": st.get("last_failure_reason"),
                "databank_layer": True,
            },
            runtime_root=root,
            broadcast_system_guard=True,
            freeze_orchestration_on_critical=True,
            rehearsal_mode=False,
        )
        halt_activated = True
        st["halt_fired_from_databank_layer"] = True
        st["last_halt_event_id"] = (halt_out or {}).get("event_id")
        _write_state(st_path, st)
    elif streak >= threshold and halted_already:
        # Do not spam duplicate activate_halt calls
        logger.warning(
            "databank write failure streak=%s >= threshold=%s but halt already active — skipping duplicate activate_halt",
            streak,
            threshold,
        )

    recovery_eligible = streak < threshold and not halted_already
    truth = {
        "truth_version": "databank_write_halt_truth_v1",
        "runtime_root": str(root),
        "threshold_configured": threshold,
        "threshold_source": st.get("threshold_source"),
        "current_failure_streak": streak,
        "halt_fired_from_databank_layer": bool(st.get("halt_fired_from_databank_layer")),
        "firing_component": component if halt_activated else None,
        "last_failure_reason": st.get("last_failure_reason"),
        "last_success_timestamp": st.get("last_success_iso"),
        "recovery_eligible": recovery_eligible,
        "recovery_condition": (
            "Streak drops below threshold after successful remote write; if kill-switch already fired, "
            "use recovery_engine + operator clear — see kill_switch_truth.json."
        ),
        "halt_activated_this_call": halt_activated,
        "skipped_duplicate_halt": streak >= threshold and halted_already and not halt_activated,
        "honesty": "Failure counts logical upsert completion after retries — matches operator-visible write_status=failed.",
    }
    _write_truth(root, truth)
    return {
        "ok": True,
        "streak": streak,
        "halt_activated": halt_activated,
        "truth": truth,
        "activate_halt_result": halt_out,
    }
