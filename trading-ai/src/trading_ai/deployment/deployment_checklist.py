"""
Deployment checklist runner — PASS/FAIL gates before live micro-validation and first-20.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.deployment.deployment_models import CheckResult, checklist_checks_template, iso_now
from trading_ai.deployment.deployment_parity import run_deployment_parity_report
from trading_ai.deployment.env_parity import run_env_parity_report
from trading_ai.deployment.first_20_protocol import ensure_first_20_protocol_files, evaluate_first_20_protocol_readiness
from trading_ai.deployment.operator_artifacts import write_all_operator_artifacts
from trading_ai.deployment.supabase_schema_readiness import run_supabase_schema_readiness
from trading_ai.deployment.paths import (
    checklist_json_path,
    checklist_txt_path,
    deployment_data_dir,
    streak_state_path,
)
from trading_ai.deployment.governance_proof import prove_governance_behavior
from trading_ai.deployment.runtime_proof_runbook import write_runtime_proof_runbook
from trading_ai.nte.databank.supabase_trade_sync import flush_unsynced_trades, report_supabase_trade_sync_diagnostics
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.production_hardening.paths import recent_order_ids_json
from trading_ai.shark.state_store import load_positions

logger = logging.getLogger(__name__)


def _validation_quote_usd() -> float:
    for key in ("LIVE_MICRO_VALIDATION_QUOTE_USD", "DEPLOYMENT_VALIDATION_QUOTE_USD"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                pass
    return 5.0


def live_micro_validation_required_env_docs() -> List[Dict[str, str]]:
    """Operator-facing list of env vars required for live micro-validation (streak gate)."""
    return [
        {
            "env": "COINBASE_EXECUTION_ENABLED",
            "required": "true, 1, or yes (or set COINBASE_ENABLED instead — both satisfy live order guard + checklist)",
            "purpose": "Enables Coinbase order placement; aliases COINBASE_ENABLED for legacy envs (see mode_context.coinbase_avenue_execution_enabled).",
        },
        {
            "env": "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM",
            "required": "YES_I_UNDERSTAND_REAL_CAPITAL",
            "purpose": "Explicit operator acknowledgment of real capital at risk.",
        },
        {
            "env": "NTE_EXECUTION_MODE",
            "required": "live (when using NTE live engine)",
            "purpose": "Must not be paper/shadow for real venue execution.",
        },
        {
            "env": "NTE_LIVE_TRADING_ENABLED",
            "required": "true when running NTE in production",
            "purpose": "Explicit enable for NTE live trading path.",
        },
        {
            "env": "EZRAS_DRY_RUN",
            "required": "unset, false, 0, or no",
            "purpose": "Dry-run must be off for real round-trip validation.",
        },
        {
            "env": "GOVERNANCE_ORDER_ENFORCEMENT",
            "required": "typically true in production",
            "purpose": "When enabled, joint_review must permit trading or orders block (bootstrap can seed a safe default).",
        },
    ]


def _streak_env_ok() -> Tuple[bool, str, Dict[str, Any]]:
    from trading_ai.nte.hardening.mode_context import coinbase_avenue_execution_enabled

    if (os.environ.get("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM") or "").strip() != "YES_I_UNDERSTAND_REAL_CAPITAL":
        return False, "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_not_set", {}
    if not coinbase_avenue_execution_enabled():
        return False, "coinbase_live_execution_not_enabled_set_COINBASE_EXECUTION_ENABLED_or_COINBASE_ENABLED", {}
    if (os.environ.get("EZRAS_DRY_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return False, "EZRAS_DRY_RUN_must_be_off", {}
    return True, "ok", {}


def _exchange_auth() -> CheckResult:
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        cc = CoinbaseClient()
        if not cc.has_credentials():
            return CheckResult(False, "coinbase_credentials_missing", {})
        accts = cc.list_all_accounts()
        if not accts:
            return CheckResult(False, "no_accounts_returned", {})
        usd = 0.0
        for a in accts:
            if not isinstance(a, dict):
                continue
            cur = str(a.get("currency") or "").upper()
            if cur not in ("USD", "USDC"):
                continue
            av = a.get("available_balance") or a.get("balance") or {}
            if isinstance(av, dict):
                try:
                    usd += float(av.get("value") or 0.0)
                except (TypeError, ValueError):
                    continue
            else:
                try:
                    usd += float(av or 0.0)
                except (TypeError, ValueError):
                    continue
        need = _validation_quote_usd()
        if usd + 1e-6 < need:
            return CheckResult(
                False,
                f"insufficient_usd_or_usdc_need_{need}_got_{round(usd, 4)}",
                {"usd_usdc": usd, "required": need, "accounts_n": len(accts)},
            )
        return CheckResult(True, "ok", {"usd_usdc": round(usd, 6), "accounts_n": len(accts), "required": need})
    except Exception as exc:
        logger.warning("exchange_auth: %s", exc)
        return CheckResult(False, f"exception:{type(exc).__name__}", {})


def _governance_proof_consistency(gov: Dict[str, Any]) -> CheckResult:
    if not gov.get("governance_proof_ok"):
        return CheckResult(False, "governance_proof_failed", {"detail": gov})
    return CheckResult(True, "ok", {"governance_proof": gov})


def _governance_trading_permitted(gov: Dict[str, Any]) -> CheckResult:
    if gov.get("governance_trading_permitted"):
        return CheckResult(True, "ok", {"governance_trading_block_reason": None})
    reason = str(
        gov.get("governance_trading_block_reason")
        or (gov.get("full_check") or {}).get("reason")
        or "unknown",
    )
    return CheckResult(
        False,
        f"governance_blocks_live_orders:{reason}",
        {
            "governance_trading_block_reason": reason,
            "joint_snapshot_summary": gov.get("joint_snapshot_summary"),
            "manual_note": (
                "MANUAL: Populate joint_review_latest.json with a valid joint review (integrity full, live mode not "
                "paused/unknown, not stale) so governance_trading_permitted becomes true under enforcement."
            ),
        },
    )


def _supabase_check() -> CheckResult:
    diag = report_supabase_trade_sync_diagnostics()
    if not diag.get("client_init_ok"):
        return CheckResult(False, "supabase_client_not_initialized", {"diag": diag})
    if not diag.get("insert_probe_ok"):
        return CheckResult(False, "supabase_insert_probe_failed", {"diag": diag})
    flush = flush_unsynced_trades()
    remaining = int(flush.get("remaining") or 0)
    if remaining > 0:
        return CheckResult(False, "unsynced_trades_remain_after_flush", {"flush": flush})
    return CheckResult(True, "ok", {"diag": diag, "flush": flush})


def _reconciliation_check() -> CheckResult:
    try:
        from trading_ai.shark.production_hardening.exchange_sync import run_exchange_reconciliation_cycle

        cyc = run_exchange_reconciliation_cycle(halt_on_divergence=False)
        if cyc.get("skipped"):
            # Layer off — still verify positions file sane
            pos = load_positions()
            orphans = [p for p in (pos.get("open_positions") or []) if isinstance(p, dict)]
            return CheckResult(True, "ok_exchange_layer_skipped", {"open_positions_n": len(orphans)})
        if not cyc.get("ok"):
            return CheckResult(False, "reconciliation_issues", {"cycle": cyc})
        return CheckResult(True, "ok", {"cycle": cyc})
    except Exception as exc:
        return CheckResult(False, f"reconciliation_exception:{type(exc).__name__}", {})


def _validation_streak_ready() -> CheckResult:
    ok, reason, extra = _streak_env_ok()
    if not ok:
        merged = {**extra, "required_live_execution_env": live_micro_validation_required_env_docs()}
        return CheckResult(False, reason, merged)
    try:
        from trading_ai.core.system_guard import trading_halt_path

        if trading_halt_path().is_file():
            return CheckResult(False, "trading_halt_file_present", {})
    except Exception:
        pass
    try:
        from trading_ai.shark.production_hardening.anomalies import anomalies_jsonl

        p = anomalies_jsonl()
        if p.is_file() and p.stat().st_size > 2_000_000:
            return CheckResult(False, "anomalies_log_very_large_review", {"path": str(p)})
    except Exception:
        pass
    return CheckResult(
        True,
        "ok",
        {"required_live_execution_env": live_micro_validation_required_env_docs()},
    )


def _soak_ready() -> CheckResult:
    try:
        from trading_ai.global_layer.review_storage import ReviewStorage

        st = ReviewStorage()
        st.ensure_review_files()
    except Exception as exc:
        return CheckResult(False, f"review_storage:{type(exc).__name__}", {})
    p = recent_order_ids_json()
    try:
        if p.is_file():
            json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(False, f"recent_order_ids_corrupt:{exc}", {})
    try:
        from trading_ai.core.system_guard import trading_halt_path

        hp = trading_halt_path()
        if hp.is_file():
            raw = json.loads(hp.read_text(encoding="utf-8"))
            reason = str(raw.get("reason") or "")
            if "stale" in reason.lower():
                return CheckResult(False, "halt_state_marked_stale", {"halt": raw})
    except Exception:
        pass
    return CheckResult(True, "ok", {})


def _observability_check() -> CheckResult:
    try:
        from trading_ai.control.command_center import run_command_center_snapshot

        run_command_center_snapshot(write_files=False)
    except Exception as exc:
        return CheckResult(False, f"command_center_failed:{type(exc).__name__}", {})
    try:
        from trading_ai.review.daily_diagnosis import _utc_today, run_daily_diagnosis

        run_daily_diagnosis(write_files=False)
    except Exception as exc:
        return CheckResult(False, f"daily_diagnosis_failed:{type(exc).__name__}", {})
    try:
        from trading_ai.review.ceo_review_session import build_ceo_daily_review
        from trading_ai.review.daily_diagnosis import _utc_today

        build_ceo_daily_review({"date": str(_utc_today()), "health": "ok", "metrics": {}})
    except Exception as exc2:
        return CheckResult(False, f"ceo_review_build_failed:{type(exc2).__name__}", {})
    try:
        from trading_ai.learning.paths import learning_data_dir

        d = learning_data_dir()
        if not os.access(d, os.W_OK):
            return CheckResult(False, "learning_data_not_writable", {})
    except Exception as exc:
        return CheckResult(False, f"memory_path:{type(exc).__name__}", {})
    return CheckResult(True, "ok", {})


def _read_streak_state() -> Dict[str, Any]:
    p = streak_state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def run_deployment_checklist(*, write_files: bool = True) -> Dict[str, Any]:
    """
    Evaluate deployment gates A–I, write JSON + TXT under ``data/deployment``.

    ``ready_for_first_20`` stays false until :func:`compute_final_readiness` confirms
    (streak + proofs + no halt). This runner sets it false if streak not passed.
    """
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    ensure_first_20_protocol_files()

    checks = checklist_checks_template()
    blocking: List[str] = []

    r = _exchange_auth()
    checks["exchange_auth_ok"] = r.to_dict()
    if not r.ok:
        blocking.append(f"exchange_auth:{r.reason}")

    gov = prove_governance_behavior(write_file=write_files)
    r = _governance_proof_consistency(gov)
    checks["governance_env_ok"] = r.to_dict()
    if not r.ok:
        blocking.append(f"governance:{r.reason}")

    r_gt = _governance_trading_permitted(gov)
    checks["governance_trading_permitted_ok"] = r_gt.to_dict()
    if not r_gt.ok:
        blocking.append(f"governance_trading:{r_gt.reason}")

    r = _supabase_check()
    checks["supabase_ok"] = r.to_dict()
    if not r.ok:
        blocking.append(f"supabase:{r.reason}")

    schema_doc = run_supabase_schema_readiness(write_file=write_files)
    schema_ok = bool(schema_doc.get("schema_ready") or schema_doc.get("supabase_schema_ready"))
    checks["supabase_schema_ok"] = {
        "ok": schema_ok,
        "reason": ",".join(schema_doc.get("blocking_reasons") or []) or "ok",
        "details": {
            "required_migrations": schema_doc.get("required_migrations"),
            "missing_remote_objects": schema_doc.get("missing_remote_objects"),
            "migration_inventory_ok": schema_doc.get("migration_inventory_ok"),
            "remote_schema_verified": schema_doc.get("remote_schema_verified"),
            "remote_verify_reason": schema_doc.get("remote_verify_reason"),
        },
    }
    if not schema_ok:
        blocking.append("supabase_schema:" + ",".join(schema_doc.get("blocking_reasons") or ["not_ready"]))

    parity_doc = run_deployment_parity_report(write_file=write_files)
    checks["deployment_parity_ok"] = {
        "ok": bool(parity_doc.get("deployment_parity_ready")),
        "reason": ",".join(parity_doc.get("blocking_reasons") or []) or "ok",
        "details": {
            "proof_environment_gaps": parity_doc.get("proof_environment_gaps"),
            "placeholders_found": parity_doc.get("placeholders_found"),
            "railway_hints": parity_doc.get("railway_hints"),
            "procfile": parity_doc.get("procfile"),
            "writable_paths": parity_doc.get("writable_paths"),
            "required_runtime_envs_present": parity_doc.get("required_runtime_envs_present"),
        },
    }
    if not parity_doc.get("deployment_parity_ready"):
        blocking.append("deployment_parity:" + ",".join(parity_doc.get("blocking_reasons") or ["not_ready"]))

    r = _reconciliation_check()
    checks["reconciliation_ok"] = r.to_dict()
    if not r.ok:
        blocking.append(f"reconciliation:{r.reason}")

    r = _validation_streak_ready()
    checks["validation_streak_ready"] = r.to_dict()
    if not r.ok:
        blocking.append(f"validation_streak:{r.reason}")

    f20 = evaluate_first_20_protocol_readiness()
    checks["first_20_protocol_ready"] = {
        "ok": f20.get("first_20_protocol_ready"),
        "reason": ";".join(f20.get("reasons") or []) or "ok",
        "details": f20,
    }
    if not f20.get("first_20_protocol_ready"):
        blocking.append("first_20_protocol:" + ",".join(f20.get("reasons") or ["not_ready"]))

    env_doc = run_env_parity_report(write_file=write_files)
    checks["env_parity_ok"] = {
        "ok": env_doc.get("env_parity_ok"),
        "reason": ",".join(env_doc.get("blocking_reasons") or []) or "ok",
        "details": {
            "runtime_root_persistent": env_doc.get("runtime_root_persistent"),
            "nte_memory_writable": env_doc.get("nte_memory_writable"),
            "placeholders_found": env_doc.get("placeholders_found"),
        },
    }
    if not env_doc.get("env_parity_ok"):
        blocking.append("env_parity")

    r = _soak_ready()
    checks["soak_ready"] = r.to_dict()
    if not r.ok:
        blocking.append(f"soak:{r.reason}")

    r = _observability_check()
    checks["observability_ok"] = r.to_dict()
    if not r.ok:
        blocking.append(f"observability:{r.reason}")

    streak = _read_streak_state()
    streak_passed = bool(streak.get("live_validation_streak_passed"))

    critical_ok = (
        checks["exchange_auth_ok"]["ok"]
        and checks["governance_env_ok"]["ok"]
        and checks["governance_trading_permitted_ok"]["ok"]
        and checks["supabase_ok"]["ok"]
        and checks["supabase_schema_ok"]["ok"]
        and checks["deployment_parity_ok"]["ok"]
        and checks["reconciliation_ok"]["ok"]
        and checks["validation_streak_ready"]["ok"]
        and checks["first_20_protocol_ready"]["ok"]
        and checks["env_parity_ok"]["ok"]
        and checks["soak_ready"]["ok"]
        and checks["observability_ok"]["ok"]
    )

    ready_micro = critical_ok and len(blocking) == 0
    ready_first20 = bool(streak_passed and critical_ok)

    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "runtime_root": str(ezras_runtime_root()),
        "ready_for_live_micro_validation": ready_micro,
        "ready_for_first_20": ready_first20,
        "blocking_reasons": blocking,
        "checks": checks,
        "live_validation_streak_state": streak,
        "required_live_execution_env": live_micro_validation_required_env_docs(),
        "notes": "ready_for_first_20 requires streak file live_validation_streak_passed plus this checklist; confirm with compute_final_readiness()",
    }

    if write_files:
        try:
            art = write_all_operator_artifacts()
            out["operator_artifacts_written"] = art
        except Exception as exc:
            logger.warning("operator_artifacts: %s", exc)
            out["operator_artifacts_written"] = {"error": str(exc)}
        checklist_json_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        lines = [
            "DEPLOYMENT CHECKLIST (single gate for micro-validation)",
            "======================================================",
            f"Generated: {out['generated_at']}",
            f"ready_for_live_micro_validation: {'YES' if ready_micro else 'NO'}",
            f"ready_for_first_20 (hint only): {'YES' if ready_first20 else 'NO'}",
            "",
            "GLOBAL BLOCKERS (must be empty for PASS):",
        ]
        if blocking:
            for b in blocking:
                lines.append(f"  - {b}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append(f"{'CHECK ID':<34} {'RESULT':<6} BLOCKER / DETAIL")
        lines.append("-" * 90)
        for k, v in checks.items():
            if isinstance(v, dict):
                ok = v.get("ok")
                result = "PASS" if ok else "FAIL"
                reason = (v.get("reason") or "").strip() or ("-" if ok else "failed")
                if len(reason) > 52:
                    reason = reason[:49] + "..."
                lines.append(f"{k:<34} {result:<6} {reason}")
            else:
                lines.append(f"{k:<34} {'?':<6} (non-dict)")
        lines.append("")
        lines.append("All checks must show PASS for ready_for_live_micro_validation = YES.")
        checklist_txt_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
        write_runtime_proof_runbook(write_file=True)

    return out
