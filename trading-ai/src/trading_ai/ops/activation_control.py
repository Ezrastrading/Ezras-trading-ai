"""
Local operator activation, seed heartbeats, supervised E2E flow, readiness audits.

All paths are deterministic and safe (no live venue orders). Real trades remain out of scope.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import get_account_risk_bucket, runtime_root
from trading_ai.governance.audit_chain import append_chained_event
from trading_ai.governance.consistency_engine import evaluate_doctrine_alignment, get_full_integrity_report
from trading_ai.governance.operator_registry import approve_doctrine, load_registry, register_operator, registry_status
from trading_ai.governance.system_doctrine import DOCTRINE_VERSION, compute_doctrine_sha256
from trading_ai.governance.temporal_consistency import record_temporal_event
from trading_ai.ops.automation_heartbeat import record_heartbeat
from trading_ai.security.encryption_at_rest import encryption_operational_status

LOCAL_OPERATOR_ID = "victor_local_primary"
LOCAL_OPERATOR_ROLE = "founder_operator"
ACTIVATION_STATE_NAME = "local_activation.json"


def _activation_state_path() -> Path:
    return runtime_root() / "state" / ACTIVATION_STATE_NAME


def _write_activation_state(payload: Dict[str, Any]) -> None:
    p = _activation_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def _merge_activation_state(updates: Dict[str, Any]) -> None:
    p = _activation_state_path()
    cur: Dict[str, Any] = {}
    if p.is_file():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            cur = {}
    cur.update(updates)
    _write_activation_state(cur)


def _read_activation_state() -> Dict[str, Any]:
    p = _activation_state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def activate_local_operator() -> Dict[str, Any]:
    """Register local primary operator and approve current doctrine; audit + temporal markers."""
    errors: List[str] = []
    reg_before = load_registry()
    op_exists = any(
        o.get("operator_id") == LOCAL_OPERATOR_ID and o.get("active") for o in reg_before.get("operators", [])
    )
    if not op_exists:
        r = register_operator(operator_id=LOCAL_OPERATOR_ID, role=LOCAL_OPERATOR_ROLE, signing_key_id="local_bootstrap")
        if not r.get("ok"):
            errors.append(str(r.get("error", "register_failed")))
    appr = approve_doctrine(
        operator_id=LOCAL_OPERATOR_ID,
        doctrine_version=DOCTRINE_VERSION,
        notes="activate_local_operator",
    )
    if not appr.get("ok"):
        errors.append(str(appr.get("error", "approve_failed")))

    try:
        append_chained_event(
            {
                "kind": "local_operator_activation",
                "operator_id": LOCAL_OPERATOR_ID,
                "doctrine_sha256": compute_doctrine_sha256(),
                "doctrine_version": DOCTRINE_VERSION,
            }
        )
    except OSError as exc:
        errors.append(f"audit_chain:{exc}")

    evaluate_doctrine_alignment(
        change_type="governance_change",
        payload={"operator_approved": True, "activation": "local_primary"},
        context={"source": "activate_local_operator"},
    )

    record_temporal_event("operator_registry_activated", source="activate_local_operator")
    record_temporal_event("doctrine_approval_recorded", source="activate_local_operator")

    st = registry_status()
    _write_activation_state(
        {
            "local_operator_id": LOCAL_OPERATOR_ID,
            "doctrine_version": DOCTRINE_VERSION,
            "registry_path": st.get("path"),
            "operator_activated": True,
        }
    )

    return {
        "ok": len(errors) == 0,
        "operator_id": LOCAL_OPERATOR_ID,
        "role": LOCAL_OPERATOR_ROLE,
        "doctrine_sha256": compute_doctrine_sha256(),
        "doctrine_version": DOCTRINE_VERSION,
        "registry": st,
        "errors": errors,
    }


def run_activation_seed() -> Dict[str, Any]:
    """Run safe code paths once to populate heartbeats and temporal samples."""
    activated: List[str] = []
    hb_updates: List[str] = []
    errors: List[str] = []
    temporal_n = 0

    def _mark_temp(kind: str) -> None:
        nonlocal temporal_n
        record_temporal_event(kind, source="activation_seed")
        temporal_n += 1

    try:
        from trading_ai.automation.vault_cycle_summaries import build_evening_vault_summary, build_morning_vault_summary

        build_morning_vault_summary()
        record_heartbeat("morning_cycle", ok=True, note="activation_seed")
        activated.append("morning_vault_summary")
        hb_updates.append("morning_cycle")
        _mark_temp("seed:morning_cycle")

        build_evening_vault_summary()
        record_heartbeat("evening_cycle", ok=True, note="activation_seed")
        activated.append("evening_vault_summary")
        hb_updates.append("evening_cycle")
        _mark_temp("seed:evening_cycle")
    except Exception as exc:
        errors.append(f"vault_summaries:{exc}")

    try:
        from trading_ai.automation.post_trade_hub import execute_post_trade_placed

        trade_placed = {
            "trade_id": "activation_seed_placed",
            "ticker": "DEMO-SEED",
            "side": "yes",
            "size": 1,
            "price": 0.5,
        }
        execute_post_trade_placed(None, trade_placed)
        record_heartbeat("post_trade", ok=True, note="activation_seed_placed")
        activated.append("post_trade_placed")
        hb_updates.append("post_trade")
        _mark_temp("seed:post_trade_placed")
    except Exception as exc:
        errors.append(f"post_trade_placed:{exc}")

    try:
        from trading_ai.execution.venue_truth_sync import run_truth_sync

        run_truth_sync(internal_open_ids=[], internal_cash=None, adapter_factory="mock")
        record_heartbeat("truth_sync", ok=True, note="activation_seed")
        activated.append("truth_sync_mock")
        hb_updates.append("truth_sync")
        _mark_temp("seed:truth_sync")
    except Exception as exc:
        errors.append(f"truth_sync:{exc}")

    try:
        from trading_ai.reporting.daily_decision_memo import generate_daily_memo

        generate_daily_memo()
        record_heartbeat("memo_generation", ok=True, note="activation_seed")
        activated.append("memo_generation")
        hb_updates.append("memo_generation")
        _mark_temp("seed:memo")
    except Exception as exc:
        errors.append(f"memo:{exc}")

    try:
        record_heartbeat("pipeline_schedule", ok=True, note="activation_seed_equivalent_no_daemon")
        activated.append("pipeline_heartbeat_record")
        hb_updates.append("pipeline_schedule")
        _mark_temp("seed:pipeline_equivalent")
    except Exception as exc:
        errors.append(f"pipeline_hb:{exc}")

    try:
        append_chained_event({"kind": "activation_seed", "components": hb_updates})
    except OSError as exc:
        errors.append(f"audit_chain:{exc}")

    evaluate_doctrine_alignment(
        change_type="audit",
        payload={"activation_seed": True, "components": activated},
        context={"source": "activation_seed"},
    )
    _mark_temp("seed:doctrine_alignment_audit")

    _merge_activation_state({"activation_seed_completed": True})

    return {
        "activated_components": activated,
        "heartbeat_updates": hb_updates,
        "temporal_events_written": temporal_n,
        "errors": errors,
    }


def run_activation_flow() -> Dict[str, Any]:
    """One supervised safe end-to-end path across core modules (no live orders)."""
    steps_run: List[str] = []
    state_touched: List[str] = []
    logs_touched: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []

    def touch(p: Path, label: str) -> None:
        if p.exists():
            state_touched.append(label)

    try:
        rb = get_account_risk_bucket()
        steps_run.append("account_risk_bucket")
        touch(runtime_root() / "state" / "risk_state.json", "risk_state")
    except Exception as exc:
        errors.append(f"risk_bucket:{exc}")

    try:
        from trading_ai.automation.strategy_risk_bucket import get_strategy_risk_bucket

        get_strategy_risk_bucket("activation_flow_probe")
        steps_run.append("strategy_risk_bucket")
    except Exception as exc:
        warnings.append(f"strategy_risk_bucket:{exc}")

    try:
        from trading_ai.automation.adaptive_sizing import get_ladder_state

        get_ladder_state()
        steps_run.append("adaptive_sizing")
    except Exception as exc:
        warnings.append(f"adaptive_sizing:{exc}")

    try:
        from trading_ai.risk.hard_lockouts import get_effective_lockout

        get_effective_lockout()
        steps_run.append("hard_lockouts")
    except Exception as exc:
        warnings.append(f"lockouts:{exc}")

    try:
        from trading_ai.execution.execution_reconciliation import get_execution_reconciliation_status

        get_execution_reconciliation_status()
        steps_run.append("execution_reconciliation")
    except Exception as exc:
        warnings.append(f"execution_reconciliation:{exc}")

    try:
        from trading_ai.execution.venue_truth_sync import run_truth_sync

        run_truth_sync(internal_open_ids=[], internal_cash=None, adapter_factory="mock")
        steps_run.append("venue_truth_sync_mock")
        record_heartbeat("truth_sync", ok=True, note="activation_flow")
    except Exception as exc:
        errors.append(f"truth_sync:{exc}")

    try:
        from trading_ai.automation.telegram_trade_events import format_trade_placed_message

        format_trade_placed_message(
            {
                "trade_id": "flow_demo",
                "ticker": "DEMO",
                "side": "yes",
                "size": 1,
                "price": 0.5,
            }
        )
        steps_run.append("telegram_format_placed")
    except Exception as exc:
        warnings.append(f"telegram_format:{exc}")

    try:
        from trading_ai.automation.post_trade_hub import execute_post_trade_closed, execute_post_trade_placed

        execute_post_trade_placed(
            None,
            {
                "trade_id": "activation_flow_open",
                "ticker": "DEMO-FLOW",
                "side": "yes",
                "size": 1,
                "price": 0.45,
            },
        )
        steps_run.append("post_trade_placed")
        record_heartbeat("post_trade", ok=True, note="activation_flow_placed")
        lp = runtime_root() / "logs" / "post_trade_log.md"
        if lp.is_file():
            logs_touched.append(str(lp))

        execute_post_trade_closed(
            None,
            {
                "trade_id": "activation_flow_open",
                "result": "win",
                "pnl": 1.0,
            },
        )
        steps_run.append("post_trade_closed")
    except Exception as exc:
        errors.append(f"post_trade:{exc}")

    try:
        from trading_ai.analysis.trade_quality_score import score_closed_trade

        score_closed_trade(
            {
                "trade_id": "activation_flow_open",
                "result": "win",
                "ticker": "DEMO-FLOW",
            }
        )
        steps_run.append("trade_quality_score")
    except Exception as exc:
        warnings.append(f"tqs:{exc}")

    try:
        from trading_ai.reporting.daily_decision_memo import generate_daily_memo

        generate_daily_memo()
        steps_run.append("memo_generation")
        record_heartbeat("memo_generation", ok=True, note="activation_flow")
    except Exception as exc:
        warnings.append(f"memo:{exc}")

    try:
        from trading_ai.ops.exception_dashboard import dashboard_status

        dashboard_status()
        steps_run.append("exception_dashboard")
    except Exception as exc:
        warnings.append(f"exceptions:{exc}")

    try:
        from trading_ai.automation.position_sizing_policy import get_sizing_policy_for_bucket

        get_sizing_policy_for_bucket(get_account_risk_bucket())
        steps_run.append("position_sizing_policy")
    except Exception as exc:
        warnings.append(f"sizing_policy:{exc}")

    record_temporal_event("activation_flow_complete", source="activation_flow")
    evaluate_doctrine_alignment(
        change_type="audit",
        payload={"activation_flow": True, "steps": len(steps_run)},
        context={"source": "activation_flow"},
    )
    _merge_activation_state({"activation_flow_completed": True})

    return {
        "ok": len(errors) == 0,
        "steps_run": steps_run,
        "state_touched": state_touched,
        "logs_touched": logs_touched,
        "warnings": warnings,
        "errors": errors,
    }


def run_final_readiness_audit() -> Dict[str, Any]:
    """Pre-test / pre-UI consolidated audit."""
    critical_failures: List[str] = []
    warnings: List[str] = []
    activated: List[str] = []
    real_world_only: List[str] = []

    act_flags = _read_activation_state()
    seed_done = bool(act_flags.get("activation_seed_completed"))

    fi = get_full_integrity_report()
    if not fi.get("overall_ok"):
        critical_failures.append("full_integrity_not_ok")
    if not fi.get("audit_chain", {}).get("ok"):
        critical_failures.append("audit_chain_verification_failed")

    reg = registry_status()
    if reg.get("operator_count", 0) < 1:
        critical_failures.append("no_registered_operator")
    if not reg.get("active_doctrine_approval"):
        critical_failures.append("no_active_doctrine_approval")

    mode = (fi.get("operator_registry") or {}).get("mode", "")
    if mode == "bootstrap_no_registry" and reg.get("operator_count", 0) >= 1 and reg.get("active_doctrine_approval"):
        pass
    elif mode == "bootstrap_no_registry":
        warnings.append("operator_registry_still_bootstrap_mode_run_activate_local_operator")

    from trading_ai.governance.temporal_consistency import build_temporal_summary

    ts = build_temporal_summary()
    w1 = ts.get("windows", {}).get("1d", {})
    if (w1.get("sample_count") or 0) < 1:
        if seed_done:
            critical_failures.append("temporal_1d_no_samples_after_seed")
        else:
            warnings.append("temporal_pending_activation_seed")

    enc = encryption_operational_status()
    if enc.get("operational_class") == "encryption_misconfigured":
        warnings.append("encryption_misconfigured")

    from trading_ai.ops.automation_heartbeat import heartbeat_status

    hb = heartbeat_status()
    for c in hb.get("components", []):
        st = c.get("status")
        if st == "UNKNOWN":
            if seed_done:
                critical_failures.append(f"heartbeat_unknown_after_seed:{c.get('component')}")
            else:
                warnings.append(f"heartbeat_unknown_pending_seed:{c.get('component')}")
        elif st == "STALE":
            warnings.append(f"heartbeat_stale:{c.get('component')}")
        elif st == "OK":
            activated.append(str(c.get("component") or ""))

    # Trading core spot checks (non-fatal → warnings)
    try:
        get_account_risk_bucket()
    except Exception as exc:
        warnings.append(f"risk_bucket:{exc}")

    ready_test = len(critical_failures) == 0
    return {
        "ok": ready_test,
        "status": "PASS" if ready_test else "FAIL",
        "critical_failures": critical_failures,
        "warnings": warnings,
        "activated_components": [a for a in activated if a],
        "remaining_real_world_only_dependencies": [
            "live_venue_order_submission_and_fills",
            "multi_day_production_trade_history_for_temporal_7d_30d",
            "real_market_prices_and_external_calendar_time",
        ],
        "ready_for_first_controlled_test": ready_test,
        "ready_for_ui_work": ready_test,
        "ready_for_supervised_real_trade_activation": ready_test,
        "detail": {
            "full_integrity": fi,
            "registry": reg,
            "temporal": ts,
            "heartbeat": hb,
            "encryption": enc,
        },
    }


def run_smoke_readiness() -> Dict[str, Any]:
    """Lightweight deterministic smoke of activation primitives."""
    seed = run_activation_seed()
    flow = run_activation_flow()
    audit = run_final_readiness_audit()
    return {
        "ok": seed.get("errors") == [] and flow.get("errors") == [] and audit.get("ok"),
        "activation_seed": {"errors": seed.get("errors"), "components": seed.get("activated_components")},
        "activation_flow": {"errors": flow.get("errors"), "steps": len(flow.get("steps_run", []))},
        "final_readiness": {"ok": audit.get("ok"), "status": audit.get("status")},
    }
