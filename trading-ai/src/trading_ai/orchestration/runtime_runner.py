"""Interval-based production runner: single-instance lock, heartbeat, truthful modes."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional, cast

from trading_ai.orchestration.orchestration_truth import write_all_orchestration_artifacts
from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.safety.failsafe_guard import load_failsafe_state, load_kill_switch
from trading_ai.storage.storage_adapter import LocalStorageAdapter

RunnerMode = Literal[
    "disabled",
    "tick_only",
    "paper_execution",
    "live_execution",
    "supervised_live",
    "autonomous_live",
]

_LOCK = "data/control/runtime_runner.lock"
_HB = "data/control/runtime_runner_heartbeat.json"
_LAST_OK = "data/control/runtime_runner_last_success.json"
_LAST_FAIL = "data/control/runtime_runner_last_failure.json"
_CYCLES = "data/control/runtime_runner_last_cycle.json"
_FAILS = "data/control/runtime_runner_failures.jsonl"
_TRUTH = "data/control/runtime_runner_truth.json"
_STATE = "data/control/runtime_runner_state.json"
_ACT_MATRIX = "data/control/runtime_runner_activation_matrix.json"
_HISTORY = "data/control/runtime_runner_cycle_history.jsonl"
_HEALTH = "data/control/runtime_runner_health.json"
_FAILURE_STATE = "data/control/runtime_runner_failure_model.json"
_LIVE_BLOCKERS = "data/control/runtime_runner_live_blockers.json"


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def global_runner_mode() -> RunnerMode:
    raw = (os.environ.get("EZRAS_RUNNER_MODE") or "disabled").strip().lower()
    if raw in ("disabled", "tick_only", "paper_execution", "live_execution", "supervised_live", "autonomous_live"):
        return raw  # type: ignore[return-value]
    return "disabled"


def avenue_runner_mode(avenue_id: str) -> RunnerMode:
    """Per-avenue override: EZRAS_RUNNER_MODE_A=tick_only etc.; else global."""
    k = f"EZRAS_RUNNER_MODE_{avenue_id.strip().upper()}"
    raw = (os.environ.get(k) or "").strip().lower()
    if raw in ("disabled", "tick_only", "paper_execution", "live_execution", "supervised_live", "autonomous_live"):
        return raw  # type: ignore[return-value]
    return global_runner_mode()


def compute_runner_readiness(*, runtime_root: Path) -> Dict[str, Any]:
    """Separate booleans — never collapse to one flag."""
    mode = global_runner_mode()
    out: Dict[str, Any] = {}
    for aid in ("A", "B", "C"):
        m = avenue_runner_mode(aid)
        sw, blockers, _ = compute_avenue_switch_live_now(aid, runtime_root=runtime_root)
        out[aid] = {
            "runner_mode": m,
            "tick_ready": m != "disabled",
            "paper_ready": m in ("paper_execution", "live_execution"),
            "live_ready": m == "live_execution" and sw,
            "autonomous_ready": False,
            "live_switch_authoritative": sw,
            "blockers_if_live": blockers,
        }
    return {"global_mode": mode, "per_avenue": out}


def avenue_switch_live_now(*, runtime_root: Path) -> bool:
    """Authoritative live switch for Avenue A (Coinbase / Gate B path)."""
    sw, _, _ = compute_avenue_switch_live_now("A", runtime_root=runtime_root)
    return bool(sw)


def _operator_live_confirmed_file(runtime_root: Path) -> bool:
    env_ok = (os.environ.get("EZRAS_OPERATOR_LIVE_CONFIRMED") or "").strip().lower() in ("1", "true", "yes")
    if env_ok:
        return True
    p = runtime_root / "data" / "control" / "operator_live_confirmation.json"
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return isinstance(raw, dict) and raw.get("confirmed") is True
    except (json.JSONDecodeError, OSError):
        return False


DaemonLiveTier = Literal["supervised", "autonomous"]


def live_execution_gate_ok(
    *,
    runtime_root: Path,
    daemon_live_tier: DaemonLiveTier = "supervised",
    require_daemon_truth: bool = True,
) -> tuple[bool, list[str]]:
    """
    ``live_execution`` / daemon live: operator confirmation + avenue switch; optionally daemon authority + runtime/env consistency.

    ``require_daemon_truth=False`` is used only when *recomputing* daemon_live_switch_authority (avoids circular read of the same file).
    """
    blockers: list[str] = []
    if not _operator_live_confirmed_file(runtime_root):
        blockers.append("operator_live_confirmation_missing")
    if not avenue_switch_live_now(runtime_root=runtime_root):
        blockers.append("avenue_switch_live_now_false")

    if not require_daemon_truth:
        return (len(blockers) == 0), blockers

    from trading_ai.orchestration.daemon_live_authority import build_daemon_runtime_consistency_truth

    ad = _ad(runtime_root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    cons = build_daemon_runtime_consistency_truth(runtime_root=runtime_root, stored_authority=auth if auth else None)
    if not cons.get("consistent_with_authoritative_artifacts"):
        blockers.append(
            "daemon_runtime_consistency:" + str(cons.get("exact_do_not_run_reason_if_inconsistent") or "mismatch")
        )
    tier = cast(DaemonLiveTier, daemon_live_tier)
    if tier == "autonomous":
        if not auth.get("avenue_a_can_run_autonomous_live_now"):
            blockers.append("daemon_live_switch_authority_denies_autonomous_live")
    else:
        if not auth.get("avenue_a_can_run_supervised_live_now"):
            blockers.append("daemon_live_switch_authority_denies_supervised_live")
    return (len(blockers) == 0), blockers


def daemon_abort_conditions(*, runtime_root: Path) -> tuple[bool, str, bool]:
    """
    Adaptive shutdown: kill switch, failsafe, emergency brake, authoritative global halt, databank unhealthy.

    Returns (should_stop, reason, is_critical).
    """
    try:
        from trading_ai.safety.kill_switch_engine import evaluate_execution_block

        blocked, halt_reason = evaluate_execution_block(runtime_root=runtime_root)
        if blocked:
            return True, halt_reason or "kill_switch_engine_blocked", True
    except Exception:
        pass
    if load_kill_switch(runtime_root=runtime_root):
        return True, "system_kill_switch", True
    st = load_failsafe_state(runtime_root=runtime_root)
    if bool(st.get("halted")):
        return True, "failsafe_halted", True
    ad = _ad(runtime_root)
    ap = ad.read_json("data/control/adaptive_live_proof.json") or {}
    if ap.get("emergency_brake_triggered") is True:
        return True, "emergency_brake_triggered", True
    gh = ad.read_json("data/control/gate_b_global_halt_truth.json") or {}
    if gh.get("global_halt_is_currently_authoritative") is True:
        return True, "global_halt_authoritative", True
    om = ad.read_json("data/control/operating_mode_state.json") or {}
    if om.get("databank_unhealthy") is True:
        return True, "databank_failure", True
    return False, "", False


def evaluate_and_persist_runtime_runner_live_blockers(
    *,
    runtime_root: Path,
    runner_mode: str,
    rebuy_path_armed: bool = False,
) -> Dict[str, Any]:
    """
    Before any live order path from this runner: record exact blockers (artifact-driven).
    Tick-only may still run when blockers exist; live steps must not proceed until cleared.
    """
    ad = _ad(runtime_root)
    if runner_mode == "disabled":
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner_mode": runner_mode,
            "rebuy_path_armed": rebuy_path_armed,
            "live_orders_allowed": False,
            "blockers": ["runner_mode_disabled"],
            "honesty": "Runner disabled — no live evaluation applicable.",
        }
        ad.write_json(_LIVE_BLOCKERS, payload)
        return payload

    blockers: list[str] = []
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    if not auth.get("truth_version"):
        blockers.append("daemon_live_switch_authority_missing_or_stale")
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    if not cons.get("consistent_with_authoritative_artifacts"):
        blockers.append(cons.get("exact_do_not_run_reason_if_inconsistent") or "daemon_runtime_consistency_failed")

    ar = ad.read_json("data/control/autonomous_live_readiness_authority.json") or {}
    if runner_mode == "supervised_live" and not auth.get("avenue_a_can_run_supervised_live_now"):
        blockers.extend(list(auth.get("exact_blockers_supervised") or [])[:12])
    if runner_mode == "autonomous_live":
        if not auth.get("avenue_a_can_run_autonomous_live_now"):
            blockers.extend(list(auth.get("exact_blockers_autonomous") or [])[:12])
        for row in ar.get("per_avenue_gate") or []:
            if row.get("avenue_id") == "A" and row.get("gate_id") == "gate_a":
                if not row.get("autonomous_live_ready"):
                    blockers.append("autonomous_live_readiness_authority_not_ready")
    if runner_mode == "live_execution":
        ok_live, live_b = live_execution_gate_ok(runtime_root=runtime_root)
        if not ok_live:
            blockers.extend(live_b)
    if rebuy_path_armed:
        rc = ad.read_json("data/control/daemon_rebuy_certification.json") or {}
        if not (rc.get("rebuy_contract_runtime_proven") or rc.get("rebuy_contract_proven_fake")):
            blockers.append("rebuy_certification_not_satisfied_for_armed_path")

    fi = ad.read_json("data/control/daemon_failure_injection_truth.json") or {}
    if not fi.get("truth_version"):
        blockers.append("daemon_failure_injection_truth_missing_optional")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_mode": runner_mode,
        "rebuy_path_armed": rebuy_path_armed,
        "live_orders_allowed": len(blockers) == 0
        and runner_mode in ("live_execution", "supervised_live", "autonomous_live"),
        "blockers": blockers,
        "honesty": "live_orders_allowed false means do not place orders from this runner; tick/logging may continue.",
    }
    ad.write_json(_LIVE_BLOCKERS, payload)
    return payload


def evaluate_continuous_daemon_runtime_proven(*, runtime_root: Path) -> bool:
    """
    True only when safety mechanisms are present and verified (typically via staging tests).

    Default false — do not claim production daemon proof without operator verification.
    """
    ad = _ad(runtime_root)
    proof = ad.read_json("data/control/runtime_runner_daemon_verification.json") or {}
    return bool(proof.get("lock_exclusivity_verified") and proof.get("failure_stop_verified"))


def try_acquire_lock(*, runtime_root: Path, pid: int) -> bool:
    p = runtime_root / _LOCK
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": pid, "started": datetime.now(timezone.utc).isoformat()}) + "\n"
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock(*, runtime_root: Path) -> None:
    p = runtime_root / _LOCK
    p.unlink(missing_ok=True)


def write_heartbeat(*, runtime_root: Path, cycle: int, note: str = "") -> None:
    ad = _ad(runtime_root)
    ad.write_json(
        _HB,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "pid": os.getpid(),
            "note": note,
        },
    )


def append_failure(msg: str, *, runtime_root: Path) -> None:
    ad = _ad(runtime_root)
    ad.ensure_parent(_FAILS)
    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(), "msg": msg},
        default=str,
    )
    with (ad.root() / _FAILS).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_runtime_runner_health(
    *,
    runtime_root: Path,
    is_running: bool,
    last_cycle_time: str,
    cycles_completed: int,
    cycles_failed: int,
    last_error: Optional[str],
    consecutive_failures: int,
    failure_types: list[str],
    last_success_time: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "is_running": is_running,
        "last_cycle_time": last_cycle_time,
        "cycles_completed": cycles_completed,
        "cycles_failed": cycles_failed,
        "last_error": last_error,
        "consecutive_failures": consecutive_failures,
        "failure_types": failure_types[-50:],
        "last_success_time": last_success_time,
        "CONTINUOUS_DAEMON_RUNTIME_PROVEN": evaluate_continuous_daemon_runtime_proven(runtime_root=runtime_root),
        "continuous_daemon_notes": (
            "CONTINUOUS_DAEMON_RUNTIME_PROVEN is true only when runtime_runner_daemon_verification.json "
            "records lock + failure-stop checks (see tests) — not implied by heartbeat alone."
        ),
    }
    if extra:
        payload.update(extra)
    _ad(runtime_root).write_json(_HEALTH, payload)
    return payload


def write_runner_truth(*, runtime_root: Path) -> Dict[str, Any]:
    ad = _ad(runtime_root)
    readiness = compute_runner_readiness(runtime_root=runtime_root)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modes_explained": {
            "disabled": "no cycles or only one-shot scripts",
            "tick_only": "scan/adapt/engine refresh; no orders",
            "paper_execution": "simulated or flagged paper paths only",
            "live_execution": "may invoke live adapters only if avenue switch_live and venue env allow",
            "supervised_live": "Avenue A daemon: live cycles with conservative pacing — see EZRAS_AVENUE_A_DAEMON_MODE",
            "autonomous_live": "Avenue A daemon: repeated cycles without per-trade confirm when ack file + proofs satisfy policy",
        },
        "avenue_a_daemon": "Use orchestration.avenue_a_live_daemon (EZRAS_AVENUE_A_DAEMON_MODE) for Coinbase Gate A loop — not EZRAS_RUNNER_MODE alone.",
        "readiness": readiness,
        "honesty": "tick_only does not prove execution; live_execution still requires per-order guards.",
    }
    ad.write_json(_TRUTH, payload)
    ad.write_text("data/control/runtime_runner_truth.txt", json.dumps(payload, indent=2) + "\n")
    act = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix": readiness["per_avenue"],
    }
    ad.write_json(_ACT_MATRIX, act)
    return payload


def run_cycle(
    *,
    runtime_root: Path,
    cycle_index: int,
    on_tick: Optional[Callable[[Path, int], None]] = None,
    failure_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One interval: refresh orchestration truth (tick), never place live orders here."""
    t0 = time.perf_counter()
    mode = global_runner_mode()
    summary: Dict[str, Any] = {
        "cycle": cycle_index,
        "mode": mode,
        "live_orders_attempted": False,
        "trades_attempted": 0,
        "trades_executed": 0,
        "errors": [],
    }
    ad = _ad(runtime_root)
    fs = failure_state or {}

    evaluate_and_persist_runtime_runner_live_blockers(runtime_root=runtime_root, runner_mode=mode)

    abort, abort_why, abort_crit = daemon_abort_conditions(runtime_root=runtime_root)
    if abort:
        summary["daemon_aborted"] = abort_why
        summary["daemon_abort_critical"] = abort_crit
        summary["ok"] = False
        summary["duration_sec"] = round(time.perf_counter() - t0, 4)
        _append_cycle_history(runtime_root, summary)
        return summary

    if mode == "live_execution":
        ok_live, live_blockers = live_execution_gate_ok(runtime_root=runtime_root, daemon_live_tier="supervised")
        if not ok_live:
            summary["live_execution_blocked"] = True
            summary["live_blockers"] = live_blockers
            summary["ok"] = False
            summary["duration_sec"] = round(time.perf_counter() - t0, 4)
            append_failure(f"live_execution_blocked:{live_blockers}", runtime_root=runtime_root)
            try:
                from trading_ai.orchestration.daemon_live_authority import write_daemon_last_gate_failure

                write_daemon_last_gate_failure(
                    runtime_root=runtime_root,
                    category="live_execution_gate",
                    detail="live_execution_blocked",
                    blockers=live_blockers,
                )
            except Exception:
                pass
            ft = list(fs.get("failure_types") or [])
            ft.append("live_execution_gate")
            fs["failure_types"] = ft
            _append_cycle_history(runtime_root, summary)
            return summary

    if mode == "supervised_live":
        ok_sup, sup_blockers = live_execution_gate_ok(runtime_root=runtime_root, daemon_live_tier="supervised")
        if not ok_sup:
            summary["daemon_live_gate_blocked"] = True
            summary["live_blockers"] = sup_blockers
            summary["ok"] = False
            summary["duration_sec"] = round(time.perf_counter() - t0, 4)
            append_failure(f"supervised_live_blocked:{sup_blockers}", runtime_root=runtime_root)
            try:
                from trading_ai.orchestration.daemon_live_authority import write_daemon_last_gate_failure

                write_daemon_last_gate_failure(
                    runtime_root=runtime_root,
                    category="supervised_live_gate",
                    detail="supervised_live_blocked",
                    blockers=sup_blockers,
                )
            except Exception:
                pass
            _append_cycle_history(runtime_root, summary)
            return summary

    if mode == "autonomous_live":
        ok_aut, aut_blockers = live_execution_gate_ok(runtime_root=runtime_root, daemon_live_tier="autonomous")
        if not ok_aut:
            summary["daemon_live_gate_blocked"] = True
            summary["live_blockers"] = aut_blockers
            summary["ok"] = False
            summary["duration_sec"] = round(time.perf_counter() - t0, 4)
            append_failure(f"autonomous_live_blocked:{aut_blockers}", runtime_root=runtime_root)
            try:
                from trading_ai.orchestration.daemon_live_authority import write_daemon_last_gate_failure

                write_daemon_last_gate_failure(
                    runtime_root=runtime_root,
                    category="autonomous_live_gate",
                    detail="autonomous_live_blocked",
                    blockers=aut_blockers,
                )
            except Exception:
                pass
            _append_cycle_history(runtime_root, summary)
            return summary

    if mode == "disabled":
        summary["skipped"] = "mode_disabled"
        summary["ok"] = True
        summary["duration_sec"] = round(time.perf_counter() - t0, 4)
        _append_cycle_history(runtime_root, summary)
        return summary

    from trading_ai.orchestration.artifact_refresh import refresh_if_stale

    def _write_all(r: Path) -> Any:
        return write_all_orchestration_artifacts(runtime_root=r)

    refresh_if_stale("avenue_orchestration_truth", _write_all, runtime_root=runtime_root)
    if mode in (
        "tick_only",
        "paper_execution",
        "live_execution",
        "supervised_live",
        "autonomous_live",
    ):
        write_runner_truth(runtime_root=runtime_root)

    if on_tick:
        on_tick(runtime_root, cycle_index)

    if mode == "live_execution":
        summary["note"] = "live_execution_mode_requires_explicit_invocation_of_venue_adapters_outside_tick_stub"
        summary["live_orders_attempted"] = False
    if mode in ("supervised_live", "autonomous_live"):
        summary["note"] = (
            "supervised_live/autonomous_live are served by trading_ai.orchestration.avenue_a_live_daemon — "
            "not this tick stub; keep EZRAS_RUNNER_MODE tick_only/disabled here if using the Avenue A daemon."
        )
        summary["live_orders_attempted"] = False

    ad.write_json(
        _LAST_OK,
        {"ts": datetime.now(timezone.utc).isoformat(), "cycle": cycle_index, "mode": mode},
    )
    ad.write_json(
        _CYCLES,
        {"last_cycle": cycle_index, "ts": datetime.now(timezone.utc).isoformat(), "mode": mode},
    )
    ad.write_json(
        _STATE,
        {"running": True, "last_cycle": cycle_index, "mode": mode},
    )
    summary["duration_sec"] = round(time.perf_counter() - t0, 4)
    _append_cycle_history(runtime_root, summary)
    summary["ok"] = True
    return summary


def _append_cycle_history(runtime_root: Path, summary: Dict[str, Any]) -> None:
    ad = _ad(runtime_root)
    ad.ensure_parent(_HISTORY)
    hist_line = json.dumps(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": summary.get("cycle"),
            "mode": summary.get("mode"),
            "summary": summary,
        },
        default=str,
    )
    with (ad.root() / _HISTORY).open("a", encoding="utf-8") as fh:
        fh.write(hist_line + "\n")


def run_forever(
    *,
    interval_seconds: float = 60.0,
    max_consecutive_failures: int = 10,
    runtime_root: Optional[Path] = None,
    on_tick: Optional[Callable[[Path, int], None]] = None,
) -> None:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    pid = os.getpid()
    if not try_acquire_lock(runtime_root=root, pid=pid):
        print("runtime runner already locked — exit", file=sys.stderr)
        sys.exit(1)

    stop = False

    def _handle_sig(*_args: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    fails = 0
    cycle = 0
    cycles_ok = 0
    cycles_bad = 0
    failure_types: list[str] = []
    last_success: Optional[str] = None
    last_err: Optional[str] = None
    fs: Dict[str, Any] = {"failure_types": failure_types}

    while not stop:
        cycle += 1
        write_heartbeat(runtime_root=root, cycle=cycle)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            summary = run_cycle(runtime_root=root, cycle_index=cycle, on_tick=on_tick, failure_state=fs)
            ok = bool(summary.get("ok"))
            if ok:
                fails = 0
                cycles_ok += 1
                last_success = now_iso
            else:
                cycles_bad += 1
                if not summary.get("live_execution_blocked") and not summary.get("daemon_live_gate_blocked"):
                    fails += 1
                reason = (
                    summary.get("daemon_aborted")
                    or summary.get("live_blockers")
                    or summary.get("skipped")
                    or "cycle_not_ok"
                )
                ft = str(reason)
                failure_types.append(ft)
                fs["failure_types"] = failure_types
                last_err = str(reason)
                if not summary.get("live_execution_blocked") and not summary.get("daemon_live_gate_blocked"):
                    append_failure(f"cycle_fail:{ft}", runtime_root=root)
                    _ad(root).write_json(
                        _LAST_FAIL,
                        {"ts": now_iso, "error": last_err, "cycle": cycle, "summary": summary},
                    )
                abort_crit = bool(summary.get("daemon_abort_critical"))
                if abort_crit:
                    append_failure("critical_daemon_abort_stop", runtime_root=root)
                    break
                if fails >= max_consecutive_failures:
                    append_failure("max_consecutive_failures_stop", runtime_root=root)
                    break
        except Exception as exc:
            cycles_bad += 1
            fails += 1
            failure_types.append(type(exc).__name__)
            last_err = str(exc)
            append_failure(str(exc), runtime_root=root)
            _ad(root).write_json(
                _LAST_FAIL,
                {"ts": datetime.now(timezone.utc).isoformat(), "error": str(exc), "cycle": cycle},
            )
            if fails >= max_consecutive_failures:
                append_failure("max_consecutive_failures_stop", runtime_root=root)
                break
        write_runtime_runner_health(
            runtime_root=root,
            is_running=True,
            last_cycle_time=now_iso,
            cycles_completed=cycles_ok,
            cycles_failed=cycles_bad,
            last_error=last_err,
            consecutive_failures=fails,
            failure_types=failure_types,
            last_success_time=last_success,
        )
        _ad(root).write_json(
            _FAILURE_STATE,
            {
                "consecutive_failures": fails,
                "failure_types": failure_types[-100:],
                "last_success_time": last_success,
                "last_error": last_err,
            },
        )
        time.sleep(max(1.0, float(interval_seconds)))
    release_lock(runtime_root=root)
    stopped = datetime.now(timezone.utc).isoformat()
    _ad(root).write_json(_STATE, {"running": False, "stopped_at": stopped})
    write_runtime_runner_health(
        runtime_root=root,
        is_running=False,
        last_cycle_time=stopped,
        cycles_completed=cycles_ok,
        cycles_failed=cycles_bad,
        last_error=last_err,
        consecutive_failures=fails,
        failure_types=failure_types,
        last_success_time=last_success,
        extra={"stopped_at": stopped},
    )
