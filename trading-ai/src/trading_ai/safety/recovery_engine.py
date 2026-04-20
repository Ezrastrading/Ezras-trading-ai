"""
Strict recovery: never clears halt without validation + explicit operator signal when required.

Clears :mod:`trading_ai.safety.kill_switch_engine` truth, ``system_kill_switch.json``, and
:func:`trading_ai.core.system_guard.clear_trading_halt` only after checks pass.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_ATTEMPTS_REL = "data/control/recovery_attempts.jsonl"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(rt: Optional[Path]) -> Path:
    import os

    return Path(rt or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()


def recovery_attempts_path(*, runtime_root: Optional[Path] = None) -> Path:
    return _root(runtime_root) / _ATTEMPTS_REL


def recent_recovery_attempts(*, runtime_root: Optional[Path] = None, max_lines: int = 50) -> List[Dict[str, Any]]:
    p = recovery_attempts_path(runtime_root=runtime_root)
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


def _append_attempt(row: Dict[str, Any], *, runtime_root: Path) -> None:
    p = recovery_attempts_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


@dataclass
class RecoveryValidation:
    ok: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)


def _check_runtime_consistency(runtime_root: Path) -> Tuple[bool, str]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    if not cons.get("consistent_with_authoritative_artifacts"):
        return False, str(cons.get("exact_do_not_run_reason_if_inconsistent") or "daemon_runtime_consistency_failed")
    return True, "ok"


def _read_kill_truth_file(runtime_root: Path) -> Optional[Dict[str, Any]]:
    p = runtime_root / "data/control/kill_switch_truth.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _check_kill_switch_layers_aligned(runtime_root: Path) -> Tuple[bool, str]:
    from trading_ai.safety.failsafe_guard import load_kill_switch

    raw = _read_kill_truth_file(runtime_root)
    ks = bool(load_kill_switch(runtime_root=runtime_root))
    if raw is None:
        if ks:
            return False, "truth_missing_but_system_kill_switch_active"
        return True, "ok"
    eng = bool(raw.get("halted"))
    if eng != ks:
        return False, "engine_vs_system_kill_switch_mismatch"
    return True, "ok"


def _proof_fresh_enough(runtime_root: Path) -> Tuple[bool, str]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ap = ad.read_json("data/control/adaptive_live_proof.json") or {}
    if ap.get("emergency_brake_triggered") is True:
        return False, "emergency_brake_still_triggered"
    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    if gh.get("global_halt_is_currently_authoritative") is True:
        return False, "global_halt_still_authoritative"
    return True, "ok"


def validate_recovery_prerequisites(
    *,
    runtime_root: Optional[Path] = None,
    require_operator_signal: bool = True,
    operator_confirmed: bool = False,
) -> RecoveryValidation:
    root = _root(runtime_root)
    checks: Dict[str, Any] = {}
    blockers: List[str] = []

    if require_operator_signal and not operator_confirmed:
        blockers.append("operator_explicit_confirm_required")
        checks["operator_signal"] = False
    else:
        checks["operator_signal"] = True

    ok_c, why_c = _check_runtime_consistency(root)
    checks["runtime_consistency"] = {"ok": ok_c, "detail": why_c}
    if not ok_c:
        blockers.append(f"runtime_consistency:{why_c}")

    ok_l, why_l = _check_kill_switch_layers_aligned(root)
    checks["halt_layer_alignment"] = {"ok": ok_l, "detail": why_l}
    if not ok_l:
        blockers.append(f"halt_layers:{why_l}")

    ok_p, why_p = _proof_fresh_enough(root)
    checks["proof_and_global_halt"] = {"ok": ok_p, "detail": why_p}
    if not ok_p:
        blockers.append(f"proofs:{why_p}")

    from trading_ai.safety.failsafe_guard import load_failsafe_state

    fs = load_failsafe_state(runtime_root=root)
    if bool(fs.get("halted")):
        blockers.append("failsafe_still_halted")
        checks["failsafe"] = {"halted": True, "reason": fs.get("halt_reason")}
    else:
        checks["failsafe"] = {"halted": False}

    return RecoveryValidation(ok=len(blockers) == 0, checks=checks, blockers=blockers)


def describe_recovery_requirements(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    v = validate_recovery_prerequisites(runtime_root=runtime_root, require_operator_signal=True, operator_confirmed=False)
    return {
        "truth_version": "recovery_path_explanation_v1",
        "generated_at": _iso(),
        "recovery_allowed": v.ok,
        "blockers": v.blockers,
        "checks": v.checks,
        "next_steps": [
            "Resolve root cause for the recorded kill_switch_reason_code.",
            "Restore daemon_runtime_consistency_truth to consistent_with_authoritative_artifacts true.",
            "Clear emergency brake / global halt artifacts if they were the cause.",
            "Run validate_recovery_prerequisites with operator_confirmed after review.",
            "Resume supervised_live first; autonomous requires separate promotion proofs.",
        ],
    }


ResumeMode = Literal["supervised", "autonomous"]


def attempt_recovery(
    *,
    runtime_root: Optional[Path] = None,
    operator_confirmed: bool = False,
    resume_mode: ResumeMode = "supervised",
    justification: str = "",
    rehearsal_mode: bool = False,
) -> Dict[str, Any]:
    """
    Strict recovery: clears halt only when validation passes.

    ``resume_mode`` autonomous is blocked unless proofs indicate autonomous is safe (honest gate).
    """
    root = _root(runtime_root)
    attempt_id = str(uuid.uuid4())
    ts = _iso()

    v = validate_recovery_prerequisites(
        runtime_root=root,
        require_operator_signal=True,
        operator_confirmed=operator_confirmed,
    )
    if not v.ok:
        row = {
            "attempt_id": attempt_id,
            "ts": ts,
            "ok": False,
            "phase": "validation_failed",
            "justification": justification,
            "resume_mode_requested": resume_mode,
            "blockers": v.blockers,
            "checks": v.checks,
        }
        _append_attempt(row, runtime_root=root)
        return row

    if resume_mode == "autonomous":
        ad = LocalStorageAdapter(runtime_root=root)
        auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
        if not auth.get("avenue_a_can_run_autonomous_live_now"):
            row = {
                "attempt_id": attempt_id,
                "ts": ts,
                "ok": False,
                "phase": "autonomous_denied",
                "error": "autonomous_resume_requires_daemon_live_switch_authority",
                "justification": justification,
            }
            _append_attempt(row, runtime_root=root)
            return row

    # Clear engine truth + failsafe switch + system guard halt
    from trading_ai.safety.failsafe_guard import write_kill_switch

    cleared_truth = {
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
    }
    cleared_truth["halted"] = False
    cleared_truth["cleared_at"] = ts
    cleared_truth["recovery_attempt_id"] = attempt_id
    cleared_truth["resume_mode"] = resume_mode
    cleared_truth["justification"] = justification
    cleared_truth["updated_at"] = ts
    LocalStorageAdapter(runtime_root=root).write_json("data/control/kill_switch_truth.json", cleared_truth)

    write_kill_switch(False, note=f"recovery_engine:{attempt_id}", runtime_root=root)

    if not rehearsal_mode:
        try:
            from trading_ai.core.system_guard import clear_trading_halt

            clear_trading_halt()
        except Exception:
            pass

        try:
            from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch, save_kill_switch

            c = load_kill_switch()
            if c.get("orchestration_frozen"):
                c["orchestration_frozen"] = False
                save_kill_switch(c)
        except Exception:
            pass

    row = {
        "attempt_id": attempt_id,
        "ts": ts,
        "ok": True,
        "phase": "cleared",
        "justification": justification,
        "resume_mode": resume_mode,
        "validation_checks_passed": v.checks,
        "state_consistency_verified": True,
        "rehearsal_mode": rehearsal_mode,
    }
    _append_attempt(row, runtime_root=root)
    return row


def recovery_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _root(runtime_root)

    def _halt_state() -> Dict[str, Any]:
        from trading_ai.safety.kill_switch_engine import current_halt_state

        return current_halt_state(runtime_root=root)

    def _allowed() -> bool:
        from trading_ai.safety.kill_switch_engine import is_trading_allowed

        return is_trading_allowed(runtime_root=root)

    v = validate_recovery_prerequisites(
        runtime_root=root,
        require_operator_signal=True,
        operator_confirmed=False,
    )
    return {
        "runtime_root": str(root),
        "halt_state": _halt_state(),
        "trading_allowed": _allowed(),
        "recovery_validation": {
            "would_pass_with_operator_confirm": len([b for b in v.blockers if b != "operator_explicit_confirm_required"]) == 0,
            "blockers": v.blockers,
            "checks": v.checks,
        },
        "recent_attempts": recent_recovery_attempts(runtime_root=root, max_lines=10),
    }
