"""
Centralized kill-switch engine: canonical halt truth, events, explanations, fail-closed evaluation.

Integrates with :mod:`trading_ai.safety.failsafe_guard` ``system_kill_switch.json`` and
:mod:`trading_ai.core.system_guard` trading halt file when activation requests full broadcast.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_REL = "data/control/kill_switch_truth.json"
_EVENTS_REL = "data/control/kill_switch_events.jsonl"
_EXPLAIN_REL = "data/control/kill_switch_explanations.json"
_REGISTRY_NAME = "kill_switch_registry.json"


def _pkg_registry_path() -> Path:
    return Path(__file__).resolve().parent / _REGISTRY_NAME


def load_trigger_registry() -> Dict[str, Any]:
    p = _pkg_registry_path()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _adapter(rt: Optional[Path]) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=rt)


def kill_switch_truth_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    return root / _TRUTH_REL


def kill_switch_events_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    return root / _EVENTS_REL


def kill_switch_explanations_path(*, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    return root / _EXPLAIN_REL


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(rel: str, row: Dict[str, Any], *, runtime_root: Path) -> None:
    ad = _adapter(runtime_root)
    p = Path(runtime_root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _read_truth_raw(runtime_root: Path) -> Optional[Dict[str, Any]]:
    p = runtime_root / _TRUTH_REL
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _default_truth() -> Dict[str, Any]:
    return {
        "truth_version": "kill_switch_truth_v1",
        "halted": False,
        "kill_switch_reason_code": None,
        "severity": None,
        "source_component": None,
        "immediate_action_required": None,
        "halt_timestamp": None,
        "avenue_id": None,
        "gate": None,
        "detail": {},
        "last_event_id": None,
        "updated_at": None,
    }


def current_halt_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Authoritative snapshot (may include mismatch flags)."""
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    base = _default_truth()
    raw = _read_truth_raw(root)
    if raw is None and (root / _TRUTH_REL).is_file():
        base["halted"] = True
        base["kill_switch_reason_code"] = "HALT_TRUTH_READ_FAILURE"
        base["severity"] = "CRITICAL"
        base["source_component"] = "kill_switch_engine"
        base["halt_timestamp"] = _iso()
        base["truth_parse_error"] = True
        return base
    if raw:
        base.update(raw)
    # Layer cross-check (fail-closed on disagreement)
    from trading_ai.safety.failsafe_guard import load_kill_switch

    ks = bool(load_kill_switch(runtime_root=root))
    eng_halted = bool(base.get("halted"))
    if ks != eng_halted:
        base["halted"] = True
        base["layer_mismatch"] = {"system_kill_switch_active": ks, "engine_truth_halted": eng_halted}
        if base.get("kill_switch_reason_code") not in ("HALT_TRUTH_MISMATCH",):
            base["kill_switch_reason_code"] = "HALT_TRUTH_MISMATCH"
            base["severity"] = "CRITICAL"
            base["source_component"] = "kill_switch_engine"
    return base


def last_halt_reason(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    st = current_halt_state(runtime_root=runtime_root)
    return {
        "kill_switch_reason_code": st.get("kill_switch_reason_code"),
        "severity": st.get("severity"),
        "source_component": st.get("source_component"),
        "halt_timestamp": st.get("halt_timestamp"),
        "detail": st.get("detail") or {},
        "immediate_action_required": st.get("immediate_action_required"),
    }


def halt_timestamp(*, runtime_root: Optional[Path] = None) -> Optional[str]:
    st = current_halt_state(runtime_root=runtime_root)
    ts = st.get("halt_timestamp")
    return str(ts) if ts else None


def is_trading_allowed(*, runtime_root: Optional[Path] = None) -> bool:
    blocked, _reason = evaluate_execution_block(runtime_root=runtime_root)
    return not blocked


@dataclass
class ExecutionBlockResult:
    blocked: bool
    halt_active_reason: str
    kill_switch_reason_code: Optional[str] = None


def evaluate_execution_block(*, runtime_root: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Fail-closed gate for execution paths.

    Returns (blocked, halt_active_reason_string) where reason is safe to surface in errors.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    st = current_halt_state(runtime_root=root)
    if st.get("truth_parse_error"):
        code = "HALT_TRUTH_READ_FAILURE"
        return True, f"halt_active_reason:{code}"

    if st.get("halted"):
        code = str(st.get("kill_switch_reason_code") or "UNKNOWN_HALT")
        return True, f"halt_active_reason:{code}"

    from trading_ai.safety.failsafe_guard import load_failsafe_state, load_kill_switch

    if load_kill_switch(runtime_root=root):
        return True, "halt_active_reason:SYSTEM_KILL_SWITCH_JSON_ACTIVE"

    fs = load_failsafe_state(runtime_root=root)
    if bool(fs.get("halted")):
        return True, f"halt_active_reason:FAILSAFE_HALTED:{fs.get('halt_reason') or 'unknown'}"

    try:
        from trading_ai.core.system_guard import get_system_guard

        g = get_system_guard()
        if g.is_trading_halted():
            return True, f"halt_active_reason:SYSTEM_GUARD:{g.halt_reason_from_file() or 'halt_file_present'}"
    except Exception:
        pass

    return False, ""


def _merge_explanation(event_id: str, explanation: Dict[str, Any], *, runtime_root: Path) -> None:
    p = kill_switch_explanations_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur: Dict[str, Any] = {"truth_version": "kill_switch_explanations_v1", "events": {}, "last_event_id": event_id}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cur = raw
                ev = cur.get("events")
                if not isinstance(ev, dict):
                    cur["events"] = {}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            cur["events"] = {}
    cur.setdefault("events", {})
    cur["events"][event_id] = explanation
    cur["last_event_id"] = event_id
    cur["updated_at"] = _iso()
    p.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_explanation(
    *,
    reason_code: str,
    source_component: str,
    severity: str,
    detail: Optional[Dict[str, Any]],
    immediate_action_required: str,
    event_id: str,
) -> Dict[str, Any]:
    reg = load_trigger_registry()
    meta = (reg.get("triggers") or {}).get(reason_code) or {}
    what = f"Kill-switch trigger {reason_code} fired from {source_component} (severity {severity})."
    why = str(meta.get("immediate_action_required") or immediate_action_required)
    safe = reason_code not in ("HALT_TRUTH_READ_FAILURE", "HALT_TRUTH_MISMATCH", "CORRUPTED_STATE_ARTIFACTS")
    return {
        "event_id": event_id,
        "what_happened": what,
        "why_it_triggered": why,
        "component_that_caused": source_component,
        "what_system_did": "Recorded halt truth, synced system_kill_switch, appended audit event, blocked new entries.",
        "what_must_happen_next": immediate_action_required,
        "safe_to_resume_supervised": False,
        "safe_to_resume_autonomous": False,
        "detail": detail or {},
        "registry_category": meta.get("category"),
        "honesty": "Resume requires recovery_engine validation plus explicit operator signal when policy demands.",
    }


def activate_halt(
    kill_switch_reason_code: str,
    *,
    source_component: str,
    severity: str,
    immediate_action_required: str,
    detail: Optional[Dict[str, Any]] = None,
    avenue_id: Optional[str] = None,
    gate: Optional[str] = None,
    runtime_root: Optional[Path] = None,
    broadcast_system_guard: bool = True,
    freeze_orchestration_on_critical: bool = True,
    rehearsal_mode: bool = False,
) -> Dict[str, Any]:
    """
    Activate canonical halt: writes truth, system kill switch JSON, events, explanations.

    ``rehearsal_mode`` suppresses orchestration freeze and system_guard halt (isolated temp roots only).
    """
    if rehearsal_mode:
        broadcast_system_guard = False
        freeze_orchestration_on_critical = False
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    event_id = str(uuid.uuid4())
    ts = _iso()
    truth = _default_truth()
    truth.update(
        {
            "halted": True,
            "kill_switch_reason_code": kill_switch_reason_code,
            "severity": severity,
            "source_component": source_component,
            "immediate_action_required": immediate_action_required,
            "halt_timestamp": ts,
            "avenue_id": avenue_id,
            "gate": gate,
            "detail": dict(detail or {}),
            "last_event_id": event_id,
            "updated_at": ts,
        }
    )
    ad = _adapter(root)
    ad.write_json(_TRUTH_REL, truth)

    note = json.dumps(
        {"kill_switch_reason_code": kill_switch_reason_code, "event_id": event_id, "source_component": source_component},
        sort_keys=True,
    )[:2000]
    from trading_ai.safety.failsafe_guard import write_kill_switch

    write_kill_switch(True, note=note, runtime_root=root)

    if broadcast_system_guard:
        try:
            from trading_ai.core.system_guard import get_system_guard

            get_system_guard().halt_now(f"kill_switch_engine:{kill_switch_reason_code}")
        except Exception:
            pass

    if freeze_orchestration_on_critical and severity.upper() == "CRITICAL" and not rehearsal_mode:
        if (os.environ.get("EZRAS_KILL_SWITCH_FREEZE_ORCHESTRATION") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
        ):
            try:
                from trading_ai.global_layer.orchestration_kill_switch import freeze_orchestration

                freeze_orchestration(True)
            except Exception:
                pass

    row = {
        "event_id": event_id,
        "type": "halt",
        "ts": ts,
        "kill_switch_reason_code": kill_switch_reason_code,
        "severity": severity,
        "source_component": source_component,
        "immediate_action_required": immediate_action_required,
        "avenue_id": avenue_id,
        "gate": gate,
        "detail": detail or {},
        "rehearsal_mode": rehearsal_mode,
    }
    _append_jsonl(_EVENTS_REL, row, runtime_root=root)

    expl = build_explanation(
        reason_code=kill_switch_reason_code,
        source_component=source_component,
        severity=severity,
        detail=detail,
        immediate_action_required=immediate_action_required,
        event_id=event_id,
    )
    _merge_explanation(event_id, expl, runtime_root=root)
    return {"ok": True, "event_id": event_id, "truth": truth}


def notify_order_failure_for_triggers(
    *,
    consecutive_failures: int,
    runtime_root: Optional[Path] = None,
    threshold: int = 5,
    rehearsal_mode: bool = False,
) -> Optional[Dict[str, Any]]:
    """Hook after order failure — evaluates repeated failure loop (threshold default matches failsafe streak scale)."""
    if consecutive_failures < threshold:
        return None
    reg = (load_trigger_registry().get("triggers") or {}).get("REPEATED_EXECUTION_FAILURE_LOOP") or {}
    ia = str(reg.get("immediate_action_required") or "halt_and_triage_execution_loop")
    return activate_halt(
        "REPEATED_EXECUTION_FAILURE_LOOP",
        source_component="execution_layer",
        severity="CRITICAL",
        immediate_action_required=ia,
        detail={"consecutive_failures": consecutive_failures, "threshold": threshold},
        runtime_root=runtime_root,
        rehearsal_mode=rehearsal_mode,
    )


def recent_events(*, runtime_root: Optional[Path] = None, max_lines: int = 50) -> List[Dict[str, Any]]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    p = root / _EVENTS_REL
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def explain_last_halt(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    st = current_halt_state(runtime_root=root)
    eid = st.get("last_event_id")
    p = kill_switch_explanations_path(runtime_root=root)
    if p.is_file() and eid:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            ev = (raw or {}).get("events") or {}
            if isinstance(ev, dict) and eid in ev:
                return {"ok": True, "explanation": ev[eid], "event_id": eid}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return {
        "ok": False,
        "error": "no_explanation_found",
        "fallback": last_halt_reason(runtime_root=root),
    }


def explain_recovery_path(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    from trading_ai.safety.recovery_engine import describe_recovery_requirements

    return describe_recovery_requirements(runtime_root=runtime_root)


def ceo_kill_switch_dashboard(*, runtime_root: Optional[Path] = None, max_events: int = 20) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    events = recent_events(runtime_root=root, max_lines=max_events)
    halts = [e for e in events if e.get("type") == "halt"]
    from trading_ai.safety.recovery_engine import recent_recovery_attempts

    recovery = recent_recovery_attempts(runtime_root=root, max_lines=max_events)
    return {
        "truth_version": "ceo_kill_switch_dashboard_v1",
        "generated_at": _iso(),
        "current_halt_state": current_halt_state(runtime_root=root),
        "recent_halts": halts[-10:],
        "recent_recovery_attempts": recovery[-10:],
        "trading_allowed": is_trading_allowed(runtime_root=root),
    }


def kill_switch_history(*, runtime_root: Optional[Path] = None, max_lines: int = 200) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    return {
        "runtime_root": str(root),
        "events": recent_events(runtime_root=root, max_lines=max_lines),
        "truth": current_halt_state(runtime_root=root),
    }
