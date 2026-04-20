"""
Kill-switch and recovery rehearsals — isolated temp runtime roots, no fake success flags.

Each scenario performs real file operations and asserts observable halt / recovery behavior.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trading_ai.safety.kill_switch_engine import (
    activate_halt,
    evaluate_execution_block,
    is_trading_allowed,
    load_trigger_registry,
    notify_order_failure_for_triggers,
)
from trading_ai.safety.recovery_engine import attempt_recovery

SetupFn = Callable[[Path], None]


def _append_result(row: Dict[str, Any], *, results_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _pkg_scenarios_path() -> Path:
    return Path(__file__).resolve().parent / "rehearsal_scenarios.json"


def load_rehearsal_scenarios() -> Dict[str, Any]:
    p = _pkg_scenarios_path()
    return json.loads(p.read_text(encoding="utf-8"))


def _write_min_consistent_truth(rt: Path) -> None:
    p = rt / "data/control/daemon_runtime_consistency_truth.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"consistent_with_authoritative_artifacts": True, "truth_version": "test"}, indent=2) + "\n",
        encoding="utf-8",
    )


def _setup_inconsistent_consistency(rt: Path) -> None:
    p = rt / "data/control/daemon_runtime_consistency_truth.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "consistent_with_authoritative_artifacts": False,
                "exact_do_not_run_reason_if_inconsistent": "rehearsal_simulated_mismatch",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _setup_supplementary_truth_for_recovery(rt: Path) -> None:
    ad = rt / "data/control/adaptive_live_proof.json"
    ad.parent.mkdir(parents=True, exist_ok=True)
    ad.write_text(json.dumps({"emergency_brake_triggered": False}, indent=2) + "\n", encoding="utf-8")
    gh = rt / "data/control/gate_b_global_halt_truth.json"
    gh.write_text(json.dumps({"global_halt_is_currently_authoritative": False}, indent=2) + "\n", encoding="utf-8")


def _setups() -> Dict[str, SetupFn]:
    reg = load_trigger_registry()
    tr = reg.get("triggers") or {}

    def inconsistent(rt: Path) -> None:
        _setup_inconsistent_consistency(rt)
        activate_halt(
            "RUNTIME_CONSISTENCY_FAILURE",
            source_component="rehearsal",
            severity="CRITICAL",
            immediate_action_required=str(tr.get("RUNTIME_CONSISTENCY_FAILURE", {}).get("immediate_action_required") or ""),
            runtime_root=rt,
            rehearsal_mode=True,
        )

    def order_loop(rt: Path) -> None:
        _write_min_consistent_truth(rt)
        _setup_supplementary_truth_for_recovery(rt)
        notify_order_failure_for_triggers(consecutive_failures=10, runtime_root=rt, threshold=5, rehearsal_mode=True)

    def supa(rt: Path) -> None:
        _write_min_consistent_truth(rt)
        _setup_supplementary_truth_for_recovery(rt)
        activate_halt(
            "SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD",
            source_component="rehearsal",
            severity="CRITICAL",
            immediate_action_required=str(
                tr.get("SUPABASE_DATABANK_WRITE_FAILURE_THRESHOLD", {}).get("immediate_action_required") or ""
            ),
            runtime_root=rt,
            rehearsal_mode=True,
        )

    def desync(rt: Path) -> None:
        _write_min_consistent_truth(rt)
        _setup_supplementary_truth_for_recovery(rt)
        from trading_ai.safety.failsafe_guard import write_kill_switch

        p = rt / "data/control/kill_switch_truth.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
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
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        write_kill_switch(True, note="rehearsal_desync", runtime_root=rt)

    def lock_breach(rt: Path) -> None:
        _write_min_consistent_truth(rt)
        _setup_supplementary_truth_for_recovery(rt)
        activate_halt(
            "LOCK_EXCLUSIVITY_VIOLATION",
            source_component="rehearsal",
            severity="CRITICAL",
            immediate_action_required=str(tr.get("LOCK_EXCLUSIVITY_VIOLATION", {}).get("immediate_action_required") or ""),
            runtime_root=rt,
            rehearsal_mode=True,
        )

    def slippage(rt: Path) -> None:
        _write_min_consistent_truth(rt)
        _setup_supplementary_truth_for_recovery(rt)
        activate_halt(
            "EXTREME_SLIPPAGE_THRESHOLD",
            source_component="rehearsal",
            severity="HIGH",
            immediate_action_required=str(tr.get("EXTREME_SLIPPAGE_THRESHOLD", {}).get("immediate_action_required") or ""),
            detail={"bps": 9999},
            runtime_root=rt,
            rehearsal_mode=True,
        )

    def corrupt(rt: Path) -> None:
        p = rt / "data/control/kill_switch_truth.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not json \n", encoding="utf-8")

    return {
        "write_inconsistent_daemon_consistency_truth": inconsistent,
        "notify_order_failure_threshold": order_loop,
        "activate_supabase_threshold": supa,
        "desync_kill_switch_layers": desync,
        "activate_lock_breach": lock_breach,
        "activate_slippage": slippage,
        "corrupt_truth_file": corrupt,
    }


def run_kill_switch_rehearsals(
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run kill-switch scenarios under isolated temp roots (or ``runtime_root`` if set — use with care)."""
    tmp: Optional[tempfile.TemporaryDirectory[str]] = None
    if runtime_root is None:
        tmp = tempfile.TemporaryDirectory(prefix="ezras_ks_rehearsal_")
        base = Path(tmp.name)
    else:
        base = Path(runtime_root).resolve()
        base.mkdir(parents=True, exist_ok=True)

    results_path = base / "data/control/kill_switch_rehearsal_results.jsonl"
    scenarios = load_rehearsal_scenarios().get("kill_switch_scenarios") or []
    setups = _setups()
    rows: List[Dict[str, Any]] = []

    old_rt = os.environ.get("EZRAS_RUNTIME_ROOT")
    try:
        for sc in scenarios:
            sid = str(sc.get("id") or "")
            setup_name = str(sc.get("setup") or "")
            sub = base / f"_rehearsal_{sid}"
            if sub.exists():
                shutil.rmtree(sub)
            sub.mkdir(parents=True)
            os.environ["EZRAS_RUNTIME_ROOT"] = str(sub)

            fn = setups.get(setup_name)
            missing_behavior: List[str] = []
            incorrect: List[str] = []
            halt_reason_sample = ""
            passed = False

            try:
                if fn is None:
                    missing_behavior.append(f"unknown_setup:{setup_name}")
                else:
                    fn(sub)
                blocked, halt_reason_sample = evaluate_execution_block(runtime_root=sub)
                if not blocked:
                    incorrect.append("execution_not_blocked_after_trigger")
                if is_trading_allowed(runtime_root=sub):
                    incorrect.append("is_trading_allowed_true_after_halt")
                ev_path = sub / "data/control/kill_switch_events.jsonl"
                if not ev_path.is_file() and setup_name not in ("corrupt_truth_file", "desync_kill_switch_layers"):
                    missing_behavior.append("events_jsonl_missing")
                if setup_name == "corrupt_truth_file":
                    if not blocked or "HALT_TRUTH_READ_FAILURE" not in halt_reason_sample:
                        incorrect.append("corrupt_truth_should_fail_closed")
                    if ev_path.is_file():
                        pass
                passed = len(incorrect) == 0 and len(missing_behavior) == 0
            except Exception as exc:
                incorrect.append(f"exception:{exc}")

            row = {
                "scenario_id": sid,
                "pass": passed,
                "missing_behavior": missing_behavior,
                "incorrect_behavior": incorrect,
                "halt_reason_sample": halt_reason_sample,
            }
            rows.append(row)
            _append_result(row, results_path=results_path)
    finally:
        if old_rt is None:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)
        else:
            os.environ["EZRAS_RUNTIME_ROOT"] = old_rt
        if tmp:
            tmp.cleanup()

    summary = {"ok": all(r.get("pass") for r in rows), "results": rows, "results_path": str(results_path)}
    return summary


def run_recovery_rehearsals() -> Dict[str, Any]:
    scenarios = load_rehearsal_scenarios().get("recovery_scenarios") or []
    results: List[Dict[str, Any]] = []
    results_path_str = ""

    with tempfile.TemporaryDirectory(prefix="ezras_rc_") as tname:
        base_path = Path(tname)
        results_path = base_path / "data/control/recovery_rehearsal_results.jsonl"
        results_path_str = str(results_path)
        old_rt = os.environ.get("EZRAS_RUNTIME_ROOT")
        try:
            for sc in scenarios:
                sub = base_path / f"rc_{sc.get('id')}"
                sub.mkdir(parents=True)
                os.environ["EZRAS_RUNTIME_ROOT"] = str(sub)

                _write_min_consistent_truth(sub)
                _setup_supplementary_truth_for_recovery(sub)
                from trading_ai.safety.failsafe_guard import default_failsafe_state, write_failsafe_state

                st = default_failsafe_state()
                st["halted"] = False
                write_failsafe_state(st, runtime_root=sub)

                activate_halt(
                    "RUNTIME_CONSISTENCY_FAILURE",
                    source_component="rehearsal",
                    severity="CRITICAL",
                    immediate_action_required="rehearsal",
                    runtime_root=sub,
                    rehearsal_mode=True,
                )

                expect_ok = bool(sc.get("expect_ok"))
                op = bool(sc.get("operator_confirmed"))
                mode = str(sc.get("resume_mode") or "supervised")
                leave_bad = bool(sc.get("leave_inconsistent_consistency"))

                if leave_bad:
                    _setup_inconsistent_consistency(sub)
                elif expect_ok:
                    _write_min_consistent_truth(sub)

                out = attempt_recovery(
                    runtime_root=sub,
                    operator_confirmed=op,
                    resume_mode="autonomous" if mode == "autonomous" else "supervised",
                    justification=f"rehearsal:{sc.get('id')}",
                    rehearsal_mode=True,
                )
                ok = bool(out.get("ok"))
                passed = ok == expect_ok
                incorrect: List[str] = []
                if ok != expect_ok:
                    incorrect.append(f"expected_ok={expect_ok} got_ok={ok}")
                if expect_ok and ok:
                    if is_trading_allowed(runtime_root=sub) is False:
                        incorrect.append("trading_still_blocked_after_successful_recovery")
                row = {
                    "scenario_id": sc.get("id"),
                    "pass": passed and len(incorrect) == 0,
                    "incorrect_behavior": incorrect,
                    "recovery_out": out,
                }
                results.append(row)
                _append_result(row, results_path=results_path)
        finally:
            if old_rt is None:
                os.environ.pop("EZRAS_RUNTIME_ROOT", None)
            else:
                os.environ["EZRAS_RUNTIME_ROOT"] = old_rt

    return {"ok": all(r.get("pass") for r in results), "results": results, "results_path": results_path_str}
