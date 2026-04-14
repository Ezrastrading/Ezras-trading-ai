"""
Controlled backend validation — safe, deterministic, supervised (no live venue orders).

Orchestrates governance, sizing, messaging, post-trade, execution audit, lockouts, and integrity rechecks.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.automation.telegram_trade_events import (
    format_trade_closed_message,
    format_trade_placed_message,
    format_trade_sizing_blocked_alert,
)
from trading_ai.governance.audit_chain import append_chained_event, verify_audit_chain
from trading_ai.governance.consistency_engine import get_consistency_status, get_full_integrity_report
from trading_ai.governance.system_doctrine import verify_doctrine_integrity
from trading_ai.governance.temporal_consistency import build_temporal_summary
from trading_ai.memory_harness.paths_harness import harness_data_dir
from trading_ai.ops.automation_heartbeat import DEFAULT_EXPECTED_INTERVALS, heartbeat_status
from trading_ai.ops.automation_scope import build_automation_scope_snapshot
from trading_ai.ops.storage_architecture import build_storage_snapshot
from trading_ai.security.encryption_at_rest import encryption_operational_status


def _report_path() -> Path:
    return runtime_root() / "logs" / "controlled_backend_test_report.md"


@contextmanager
def _runtime_env(*, explicit_root: Optional[Path], use_temp: bool):
    """Set EZRAS_RUNTIME_ROOT for explicit path or temp dir; else leave env unchanged (in-place)."""
    prev = os.environ.get("EZRAS_RUNTIME_ROOT")
    changed = False
    try:
        if explicit_root is not None:
            root = explicit_root.resolve()
            root.mkdir(parents=True, exist_ok=True)
            os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
            changed = True
            yield root
        elif use_temp:
            root = Path(tempfile.mkdtemp(prefix="ezras_controlled_"))
            os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
            changed = True
            yield root
        else:
            yield runtime_root().resolve()
    finally:
        if changed:
            if prev is None:
                os.environ.pop("EZRAS_RUNTIME_ROOT", None)
            else:
                os.environ["EZRAS_RUNTIME_ROOT"] = prev


def _seed_activation_if_needed() -> None:
    from trading_ai.ops.activation_control import activate_local_operator, run_activation_seed

    activate_local_operator()
    st_path = runtime_root() / "state" / "automation_heartbeat_state.json"
    need_hb = not st_path.is_file() or not json.loads(st_path.read_text(encoding="utf-8")).get("heartbeats")
    if need_hb:
        run_activation_seed()


def _write_report(
    *,
    ok: bool,
    scenarios: List[Dict[str, Any]],
    logs_touched: List[str],
    state_touched: List[str],
    critical_failures: List[str],
    warnings: List[str],
) -> None:
    p = _report_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    verdict = "PASS" if ok else "FAIL"
    lines = [
        f"# Controlled Backend Test Report — {ts}",
        "",
        "## Overall Verdict",
        verdict,
        "",
        "## Scenarios",
        "",
        "| Scenario | Verdict | Notes |",
        "|----------|---------|-------|",
    ]
    for s in scenarios:
        sid = s.get("id", "")
        sok = s.get("ok", False)
        notes = "; ".join(s.get("errors") or []) or "; ".join(s.get("warnings") or []) or "—"
        lines.append(f"| {sid} | {'PASS' if sok else 'FAIL'} | {notes[:200]} |")
    lines.extend(
        [
            "",
            "## Critical Failures",
            "None" if not critical_failures else "\n".join(f"- {x}" for x in critical_failures),
            "",
            "## Warnings",
            "None" if not warnings else "\n".join(f"- {x}" for x in warnings),
            "",
            "## Logs Touched",
        ]
    )
    lines.extend(f"- {x}" for x in sorted(set(logs_touched)) or ["—"])
    lines.append("")
    lines.append("## State Touched")
    lines.extend(f"- {x}" for x in sorted(set(state_touched)) or ["—"])
    lines.extend(
        [
            "",
            "## Ready for First Supervised Real Trade",
            "Yes" if ok else "No",
            "",
            "## Remaining Real-World-Only Dependencies",
            "- Live venue fills and broker execution confirmations",
            "- Real market prices and external calendar time",
            "- Long-horizon production trade history for extended temporal windows",
            "",
        ]
    )
    p.write_text("\n".join(lines), encoding="utf-8")


def _track(paths: List[str], p: Path) -> None:
    if p.is_file():
        paths.append(str(p))


def scenario_1_governance_boot(logs_touched: List[str], state_touched: List[str]) -> Dict[str, Any]:
    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    mod = verify_doctrine_integrity()
    if mod.verdict == "ALIGNED":
        checks.append("doctrine_integrity_aligned")
    else:
        errors.append(f"doctrine_integrity:{mod.verdict}")

    from trading_ai.governance.operator_registry import registry_status

    reg = registry_status()
    if reg.get("operator_count", 0) >= 1:
        checks.append("operator_registry_has_operator")
    else:
        errors.append("no_operator")

    if reg.get("active_doctrine_approval"):
        checks.append("active_doctrine_approval_present")
    else:
        errors.append("no_active_doctrine_approval")

    vr = verify_audit_chain()
    if vr.ok:
        checks.append("audit_chain_verifies")
    else:
        errors.append(f"audit_chain:{vr.detail}")

    st = get_consistency_status()
    fi = st.get("full_integrity") or {}
    if fi.get("overall_ok"):
        checks.append("consistency_full_integrity_ok")
    else:
        errors.append("consistency_full_integrity_not_ok")

    _track(logs_touched, runtime_root() / "logs" / "governance_audit_chain.jsonl")
    rp = reg.get("path")
    if rp:
        state_touched.append(str(rp))

    return {
        "id": "scenario_1_governance_boot",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_2_heartbeat_health() -> Dict[str, Any]:
    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    hb = heartbeat_status()
    if hb.get("overall") == "healthy":
        checks.append("heartbeat_overall_healthy")
    else:
        warnings.append(f"heartbeat_overall:{hb.get('overall')}:{hb.get('degraded_reasons')}")

    comps = {c["component"]: c for c in hb.get("components", [])}
    for name in DEFAULT_EXPECTED_INTERVALS:
        row = comps.get(name)
        if not row:
            errors.append(f"missing_component:{name}")
            continue
        if row.get("status") == "UNKNOWN":
            errors.append(f"unknown_heartbeat:{name}")
        else:
            checks.append(f"heartbeat_recorded:{name}:{row.get('status')}")

    ts = build_temporal_summary()
    n1 = int((ts.get("windows") or {}).get("1d", {}).get("sample_count") or 0)
    if n1 > 0:
        checks.append(f"temporal_1d_samples:{n1}")
    else:
        errors.append("temporal_1d_empty")

    return {
        "id": "scenario_2_heartbeat_activation_health",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def _write_risk_state(tmp: Path, data: Dict[str, Any]) -> None:
    p = tmp / "state" / "risk_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def scenario_3_normal_trade_open(logs_touched: List[str], state_touched: List[str]) -> Dict[str, Any]:
    from trading_ai.automation.position_sizing_policy import approve_new_trade_for_execution
    from trading_ai.automation.post_trade_hub import execute_post_trade_placed
    from trading_ai.ops.automation_heartbeat import record_heartbeat

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 100.0,
            "peak_equity_index": 100.0,
            "recent_results": [],
            "processed_close_ids": [],
        },
    )
    state_touched.append(str(runtime_root() / "state" / "risk_state.json"))

    trade = {
        "trade_id": "ctrl_normal_open",
        "capital_allocated": 100.0,
        "ticker": "CTRL-NORMAL",
        "side": "yes",
        "market": "Controlled test market",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        d = approve_new_trade_for_execution(trade)
    except Exception as exc:
        errors.append(f"approve_normal:{exc}")
        return {
            "id": "scenario_3_normal_trade_open_approval",
            "ok": False,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
        }

    if str(d.get("approval_status")) == "APPROVED" and float(d.get("approved_size") or 0) == 100.0:
        checks.append("sizing_approved_full")
    else:
        errors.append(f"unexpected_decision:{d}")

    rb = str(d.get("effective_bucket") or "")
    if rb == "NORMAL":
        checks.append("risk_bucket_normal")
    else:
        warnings.append(f"account_bucket_not_normal:{rb}")

    msg = format_trade_placed_message(trade)
    if "TRADE OPEN" in msg and "TRADE BLOCKED" not in msg:
        checks.append("telegram_open_format_ok")
    else:
        errors.append("telegram_open_format_unexpected")

    out = execute_post_trade_placed(None, trade)
    if out.get("status") in ("sent", "processed_partial", "failed", "skipped_duplicate"):
        checks.append(f"post_trade_placed_status:{out.get('status')}")
    else:
        errors.append(f"post_trade_placed:{out}")

    record_heartbeat("post_trade", ok=True, note="controlled_backend_test_normal")
    checks.append("heartbeat_post_trade")

    _track(logs_touched, runtime_root() / "logs" / "post_trade_log.md")
    _track(logs_touched, runtime_root() / "logs" / "position_sizing_log.md")
    _track(logs_touched, runtime_root() / "logs" / "pre_submit_sizing_log.md")

    return {
        "id": "scenario_3_normal_trade_open_approval",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_4_reduced_trade_open(logs_touched: List[str], state_touched: List[str]) -> Dict[str, Any]:
    from trading_ai.automation.position_sizing_policy import approve_new_trade_for_execution
    from trading_ai.automation.post_trade_hub import execute_post_trade_placed

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 98.0,
            "peak_equity_index": 100.0,
            "recent_results": ["win", "loss", "loss"],
            "processed_close_ids": [],
        },
    )

    trade = {
        "trade_id": "ctrl_reduced_open",
        "capital_allocated": 200.0,
        "ticker": "CTRL-REDUCED",
        "side": "yes",
        "market": "Controlled reduced path",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        d = approve_new_trade_for_execution(trade)
    except Exception as exc:
        errors.append(f"approve_reduced:{exc}")
        return {
            "id": "scenario_4_reduced_trade_open_approval",
            "ok": False,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
        }

    if str(d.get("approval_status")) == "REDUCED" and abs(float(d.get("approved_size") or 0) - 100.0) < 0.01:
        checks.append("reduced_halves_to_100")
    else:
        errors.append(f"reduced_decision_unexpected:{d}")

    meta = trade.get("position_sizing_meta") or {}
    req = meta.get("requested_size")
    appr = meta.get("approved_size")
    if req is not None and appr is not None and float(appr) < float(req) - 0.01:
        checks.append("requested_gt_approved_recorded_in_meta")
    else:
        errors.append("meta_mismatch_requested_approved")

    msg = format_trade_placed_message(trade)
    if "Size Adjustment: Reduced 50%" in msg or "REDUCED" in msg:
        checks.append("telegram_shows_reduced_mode")
    else:
        errors.append("telegram_missing_reduced_cue")

    execute_post_trade_placed(None, trade)
    psl = runtime_root() / "logs" / "position_sizing_log.md"
    if psl.is_file() and "ctrl_reduced_open" in psl.read_text(encoding="utf-8", errors="replace"):
        checks.append("sizing_log_mentions_trade")
    else:
        errors.append("sizing_log_missing_trade_id")

    pre = runtime_root() / "logs" / "pre_submit_sizing_log.md"
    _track(logs_touched, pre)
    _track(logs_touched, psl)

    return {
        "id": "scenario_4_reduced_trade_open_approval",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_5_blocked_trade_open(logs_touched: List[str]) -> Dict[str, Any]:
    from trading_ai.automation.position_sizing_policy import TradePlacementBlocked, approve_new_trade_for_execution

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 80.0,
            "peak_equity_index": 100.0,
            "recent_results": ["loss", "loss", "loss", "loss", "win"],
            "processed_close_ids": [],
        },
    )

    trade = {
        "trade_id": "ctrl_blocked_open",
        "capital_allocated": 100.0,
        "ticker": "CTRL-BLOCKED",
        "side": "yes",
    }
    try:
        approve_new_trade_for_execution(trade)
        errors.append("expected_TradePlacementBlocked_not_raised")
    except TradePlacementBlocked as exc:
        checks.append("trade_placement_blocked_raised")
        txt = format_trade_sizing_blocked_alert(exc.trade_snapshot or {}, exc.decision or {})
        if "TRADE BLOCKED" in txt or "Block Reason" in txt:
            checks.append("blocked_telegram_format_ok")
        else:
            errors.append("blocked_alert_format_unexpected")

    psl = runtime_root() / "logs" / "position_sizing_log.md"
    if psl.is_file() and "ctrl_blocked_open" in psl.read_text(encoding="utf-8", errors="replace"):
        checks.append("sizing_log_records_rejection")
    else:
        errors.append("blocked_rejection_not_in_sizing_log")

    ptl = runtime_root() / "logs" / "post_trade_log.md"
    if ptl.is_file() and "ctrl_blocked_open" in ptl.read_text(encoding="utf-8", errors="replace"):
        errors.append("post_trade_log_should_not_record_placed_for_blocked_trade_id")
    else:
        checks.append("no_post_trade_placed_entry_for_blocked_id")

    _track(logs_touched, psl)

    return {
        "id": "scenario_5_blocked_trade_open",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_6_closed_trade_path(logs_touched: List[str]) -> Dict[str, Any]:
    from trading_ai.automation.post_trade_hub import execute_post_trade_closed
    from trading_ai.reporting.daily_decision_memo import generate_daily_memo

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    trade = {
        "trade_id": "ctrl_normal_open",
        "ticker": "CTRL-NORMAL",
        "side": "yes",
        "result": "win",
        "capital_allocated": 100.0,
        "risk_bucket_at_open": "NORMAL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out = execute_post_trade_closed(None, trade)
    if out.get("trade_quality") is not None or out.get("status"):
        checks.append("post_trade_closed_ran")
    else:
        warnings.append("trade_quality_missing")

    cm = format_trade_closed_message(trade)
    if "TRADE CLOSED" in cm:
        checks.append("closed_message_format_ok")
    else:
        errors.append("closed_message_format")

    try:
        generate_daily_memo()
        checks.append("memo_generation_ran")
    except Exception as exc:
        errors.append(f"memo:{exc}")

    _track(logs_touched, runtime_root() / "logs" / "post_trade_log.md")

    return {
        "id": "scenario_6_closed_trade_path",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_7_execution_safe_validation(logs_touched: List[str]) -> Dict[str, Any]:
    from trading_ai.automation.position_sizing_policy import TradePlacementBlocked, approve_new_trade_for_execution
    from trading_ai.execution import kalshi_exec
    from trading_ai.execution.submission_audit import append_execution_submission_log

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    # Contract conversion parity with kalshi_exec (no venue call).
    c1 = kalshi_exec._approved_dollars_to_contracts(100.0, 0.5)
    if c1 == 200:
        checks.append("kalshi_contracts_floor_matches_expected")
    else:
        errors.append(f"contract_math:{c1}")

    append_execution_submission_log(
        trade_id="ctrl_exec_audit_abort",
        requested_size=100.0,
        approved_size=0.0,
        actual_submitted_size=0,
        bucket="BLOCKED",
        approval_status="BLOCKED",
        trading_allowed=False,
        reason="controlled_test_abort",
        extra={"venue": "kalshi", "venue_unit": "contracts", "submission_aborted": True},
    )
    checks.append("submission_audit_abort_line_written")
    elog = runtime_root() / "logs" / "execution_submission_log.md"
    _track(logs_touched, elog)
    if elog.is_file() and "ctrl_exec_audit_abort" in elog.read_text(encoding="utf-8", errors="replace"):
        checks.append("execution_submission_log_contains_trade_id")
    else:
        errors.append("execution_submission_log_missing_entry")

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 80.0,
            "peak_equity_index": 100.0,
            "recent_results": ["loss", "loss", "loss", "loss", "win"],
            "processed_close_ids": [],
        },
    )
    try:
        approve_new_trade_for_execution({"trade_id": "ctrl_no_submit", "capital_allocated": 50.0})
        errors.append("blocked_trade_should_not_approve")
    except TradePlacementBlocked:
        checks.append("blocked_trade_does_not_reach_venue_submit_path")

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 100.0,
            "peak_equity_index": 100.0,
            "recent_results": [],
            "processed_close_ids": [],
        },
    )
    checks.append("risk_state_reset_normal_after_execution_checks")

    return {
        "id": "scenario_7_execution_layer_safe_validation",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_8_truth_reconciliation_exception() -> Dict[str, Any]:
    from trading_ai.execution.execution_reconciliation import get_execution_reconciliation_status
    from trading_ai.execution.venue_truth_sync import run_truth_sync
    from trading_ai.ops.exception_dashboard import dashboard_status, list_open_exceptions

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    row = run_truth_sync(internal_open_ids=[], internal_cash=None, adapter_factory="mock")
    if isinstance(row, dict) and row.get("verdict"):
        checks.append(f"truth_sync_mock_verdict:{row.get('verdict')}")
    else:
        errors.append("truth_sync_mock_unexpected_return")

    get_execution_reconciliation_status()
    checks.append("reconciliation_status_path_ok")

    ds = dashboard_status()
    open_n = int(ds.get("open_count") or 0)
    if open_n == 0:
        checks.append("exception_dashboard_clean")
    else:
        oxs = list_open_exceptions()
        warnings.append(f"open_exceptions:{open_n}:{[e.get('category') for e in oxs[:5]]}")

    return {
        "id": "scenario_8_truth_reconciliation_exception_health",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_9_lockout_defenses() -> Dict[str, Any]:
    from trading_ai.risk.hard_lockouts import (
        can_open_new_trade,
        clear_daily_lockout_manual,
        clear_weekly_lockout_manual,
        get_effective_lockout,
        simulate_daily_loss,
    )

    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    clear_daily_lockout_manual(actor="controlled_test", reason="scenario_9_reset")
    clear_weekly_lockout_manual(actor="controlled_test", reason="scenario_9_reset")
    if can_open_new_trade()["allowed"]:
        checks.append("can_open_when_lockout_cleared")
    else:
        errors.append("should_allow_after_clear")

    simulate_daily_loss(5.0)
    lo = get_effective_lockout()
    if lo.get("effective_lockout") or lo.get("daily_lockout_active"):
        checks.append("lockout_engages_under_stress")
    else:
        errors.append("lockout_not_engaged")

    if not can_open_new_trade()["allowed"]:
        checks.append("can_open_false_when_locked")
    else:
        errors.append("can_open_should_be_false_under_lockout")

    clear_daily_lockout_manual(actor="controlled_test", reason="scenario_9_teardown")
    clear_weekly_lockout_manual(actor="controlled_test", reason="scenario_9_teardown")
    if can_open_new_trade()["allowed"]:
        checks.append("lockout_cleared_for_subsequent_checks")
    else:
        errors.append("lockout_teardown_failed")

    return {
        "id": "scenario_9_lockout_risk_defenses",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_10_storage_memory_logging(state_touched: List[str], logs_touched: List[str]) -> Dict[str, Any]:
    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    rt = runtime_root()
    checks.append(f"runtime_root_resolved:{rt}")
    (rt / "state").mkdir(parents=True, exist_ok=True)
    (rt / "logs").mkdir(parents=True, exist_ok=True)
    state_touched.append(str(rt / "state"))
    logs_touched.append(str(rt / "logs"))

    enc = encryption_operational_status()
    oc = str(enc.get("operational_class") or "")
    if oc in (
        "encryption_explicitly_disabled",
        "encryption_available_and_verified",
        "encryption_misconfigured",
    ):
        checks.append(f"encryption_status_explicit:{oc}")
    else:
        errors.append(f"encryption_unexpected:{enc}")

    hd = harness_data_dir()
    checks.append(f"memory_harness_path_resolves:{hd}")

    ch = rt / "logs" / "governance_audit_chain.jsonl"
    _track(logs_touched, ch)
    if verify_audit_chain().ok:
        checks.append("append_only_audit_chain_valid")
    else:
        errors.append("audit_chain_invalid")

    return {
        "id": "scenario_10_storage_memory_logging_consistency",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def scenario_11_final_recheck() -> Dict[str, Any]:
    checks: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    _write_risk_state(
        runtime_root(),
        {
            "version": 1,
            "equity_index": 100.0,
            "peak_equity_index": 100.0,
            "recent_results": [],
            "processed_close_ids": [],
        },
    )

    st = get_consistency_status()
    if (st.get("full_integrity") or {}).get("overall_ok"):
        checks.append("consistency_status_ok")
    else:
        errors.append("consistency_degraded")

    build_temporal_summary()
    checks.append("temporal_recheck")

    from trading_ai.config import get_settings

    build_storage_snapshot(settings=get_settings())
    checks.append("storage_snapshot_ok")

    build_automation_scope_snapshot()
    checks.append("automation_scope_ok")

    fi = get_full_integrity_report()
    if fi.get("overall_ok"):
        checks.append("integrity_report_ok")
    else:
        errors.append("integrity_report_not_ok")

    return {
        "id": "scenario_11_final_consistency_integrity_recheck",
        "ok": len(errors) == 0,
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def run_controlled_backend_test(
    *,
    isolated: bool = True,
    runtime_root_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run the full controlled validation suite.

    - Default ``isolated=True``: temporary EZRAS_RUNTIME_ROOT (no live orders; safe).
    - ``EZRAS_CONTROLLED_TEST_IN_PLACE=1`` with ``isolated=True`` is ignored; use ``isolated=False`` for in-place runs.
    - ``runtime_root_override``: use this directory (e.g. pytest tmp_path) instead of a new temp dir.
    """
    in_place = os.environ.get("EZRAS_CONTROLLED_TEST_IN_PLACE", "").strip() in ("1", "true", "True")
    use_temp = runtime_root_override is None and isolated and not in_place

    logs_touched: List[str] = []
    state_touched: List[str] = []
    scenarios: List[Dict[str, Any]] = []
    critical_failures: List[str] = []
    all_warnings: List[str] = []

    def run(fn: Callable[[], Dict[str, Any]]) -> None:
        try:
            r = fn()
            scenarios.append(r)
            sid = str(r.get("id") or "?")
            if not r.get("ok"):
                critical_failures.append(sid)
            all_warnings.extend(str(x) for x in (r.get("warnings") or []))
        except Exception as exc:
            scenarios.append(
                {
                    "id": "exception",
                    "ok": False,
                    "checks": [],
                    "warnings": [],
                    "errors": [str(exc)],
                }
            )
            critical_failures.append("exception")

    with _runtime_env(explicit_root=runtime_root_override, use_temp=use_temp):
        try:
            append_chained_event({"kind": "controlled_backend_test_start", "use_temp": use_temp})
        except OSError:
            pass

        _seed_activation_if_needed()
        state_touched.append(str(runtime_root() / "state"))

        run(lambda: scenario_1_governance_boot(logs_touched, state_touched))
        run(lambda: scenario_2_heartbeat_health())
        run(lambda: scenario_3_normal_trade_open(logs_touched, state_touched))
        run(lambda: scenario_4_reduced_trade_open(logs_touched, state_touched))
        run(lambda: scenario_5_blocked_trade_open(logs_touched))
        run(lambda: scenario_6_closed_trade_path(logs_touched))
        run(lambda: scenario_7_execution_safe_validation(logs_touched))
        run(lambda: scenario_8_truth_reconciliation_exception())
        run(lambda: scenario_9_lockout_defenses())
        run(lambda: scenario_10_storage_memory_logging(state_touched, logs_touched))
        run(lambda: scenario_11_final_recheck())

        try:
            append_chained_event(
                {
                    "kind": "controlled_backend_test_complete",
                    "ok": len(critical_failures) == 0,
                    "failed_scenarios": critical_failures,
                }
            )
        except OSError:
            pass

        ok = len(critical_failures) == 0 and all(s.get("ok") for s in scenarios)
        status = "PASS" if ok else "FAIL"

        _write_report(
            ok=ok,
            scenarios=scenarios,
            logs_touched=logs_touched,
            state_touched=state_touched,
            critical_failures=critical_failures,
            warnings=all_warnings,
        )
        logs_touched.append(str(_report_path()))

        return {
            "ok": ok,
            "status": status,
            "scenarios": scenarios,
            "logs_touched": sorted(set(logs_touched)),
            "state_touched": sorted(set(state_touched)),
            "critical_failures": critical_failures,
            "warnings": all_warnings,
            "ready_for_first_real_supervised_trade": ok,
            "report_path": str(_report_path()),
        }
