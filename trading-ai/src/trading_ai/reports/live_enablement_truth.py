"""
Unified operator-facing snapshot: credentials, governance, execution flags, and what blocks live paths.

Never stores secret values — only present/missing booleans and short reasons.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report, load_gate_b_validation_record


def _coinbase_creds_present() -> Tuple[bool, List[str]]:
    missing: List[str] = []
    has_name = bool(
        (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    )
    has_secret = bool(
        (os.environ.get("COINBASE_API_PRIVATE_KEY") or os.environ.get("COINBASE_API_SECRET") or "").strip()
    )
    if not has_name:
        missing.append("coinbase_api_key_name_or_coinbase_api_key")
    if not has_secret:
        missing.append("coinbase_api_private_key_or_coinbase_api_secret")
    return len(missing) == 0, missing


def _supabase_present() -> Tuple[bool, List[str]]:
    missing: List[str] = []
    if not (os.environ.get("SUPABASE_URL") or "").strip():
        missing.append("SUPABASE_URL")
    if not (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip():
        missing.append("SUPABASE_SERVICE_ROLE_KEY_or_SUPABASE_KEY")
    return len(missing) == 0, missing


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _live_confirm_present() -> bool:
    return (os.environ.get("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM") or "").strip() == "YES_I_UNDERSTAND_REAL_CAPITAL"


def build_live_enablement_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    lock_path = ctrl / "system_execution_lock.json"
    exec_proof = root / "execution_proof" / "live_execution_validation.json"
    dry = _truthy_env("EZRAS_DRY_RUN")
    coinbase_ok, coinbase_missing = _coinbase_creds_present()
    sb_ok, sb_missing = _supabase_present()
    gb_rep = gate_b_live_status_report()
    gb_val = load_gate_b_validation_record()

    blockers_gate_a_live_micro: List[str] = []
    if dry:
        blockers_gate_a_live_micro.append("EZRAS_DRY_RUN_enabled")
    if not coinbase_ok:
        blockers_gate_a_live_micro.extend([f"missing:{m}" for m in coinbase_missing])
    if not sb_ok:
        blockers_gate_a_live_micro.extend([f"missing:{m}" for m in sb_missing])
    if not _live_confirm_present():
        blockers_gate_a_live_micro.append("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_not_YES_I_UNDERSTAND_REAL_CAPITAL")
    if not _truthy_env("COINBASE_EXECUTION_ENABLED") and not _truthy_env("COINBASE_ENABLED"):
        blockers_gate_a_live_micro.append("COINBASE_EXECUTION_ENABLED_or_COINBASE_ENABLED_not_true")
    if not exec_proof.is_file():
        blockers_gate_a_live_micro.append("execution_proof/live_execution_validation.json_absent_until_successful_run")

    blockers_gate_b_live: List[str] = []
    if not (os.environ.get("GATE_B_LIVE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes"):
        blockers_gate_b_live.append("GATE_B_LIVE_EXECUTION_ENABLED_false")
    vr = gb_val or {}
    if not (vr.get("micro_validation_pass") and str(vr.get("validated_at") or "").strip()):
        blockers_gate_b_live.append("gate_b_validation.json_missing_micro_or_validated_at")
    if gb_rep.get("gate_b_disabled_by_runtime_policy"):
        blockers_gate_b_live.append("runtime_coinbase_policy_blocks_gate_b")
    gb_live_proof = root / "execution_proof" / "gate_b_live_execution_validation.json"
    if not gb_live_proof.is_file():
        blockers_gate_b_live.append("execution_proof/gate_b_live_execution_validation.json_absent_until_gate_b_live_micro_run")
    else:
        try:
            gpr = json.loads(gb_live_proof.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            gpr = {}
        if not isinstance(gpr, dict):
            gpr = {}
        if gpr.get("FINAL_EXECUTION_PROVEN") is not True:
            blockers_gate_b_live.append("gate_b_live_execution_validation_FINAL_EXECUTION_PROVEN_not_true")
        if gpr.get("gate_b_order_verified") is not True and gpr.get("coinbase_order_verified") is not True:
            blockers_gate_b_live.append("gate_b_live_order_verified_not_true")

    blockers_first_few_trades: List[str] = []
    blockers_first_few_trades.extend(blockers_gate_a_live_micro)
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            if not bool(lock.get("system_locked")):
                blockers_first_few_trades.append("system_execution_lock.system_locked_false")
            if not bool(lock.get("ready_for_live_execution")):
                blockers_first_few_trades.append("system_execution_lock.ready_for_live_execution_false")
            if not bool(lock.get("gate_a_enabled")):
                blockers_first_few_trades.append("system_execution_lock.gate_a_disabled")
        except (json.JSONDecodeError, OSError) as exc:
            blockers_first_few_trades.append(f"system_execution_lock_read_error:{exc}")
    else:
        blockers_first_few_trades.append("system_execution_lock.json_missing")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "runtime_root_path_exists": root.is_dir(),
        "dry_run": dry,
        "coinbase_api_configured": coinbase_ok,
        "coinbase_missing_env_vars": coinbase_missing,
        "supabase_configured": sb_ok,
        "supabase_missing_env_vars": sb_missing,
        "live_single_execution_validation_confirm_ok": _live_confirm_present(),
        "coinbase_execution_env_ok": _truthy_env("COINBASE_EXECUTION_ENABLED") or _truthy_env("COINBASE_ENABLED"),
        "gate_b_operator_flag": (os.environ.get("GATE_B_LIVE_EXECUTION_ENABLED") or "").strip().lower()
        in ("1", "true", "yes"),
        "gate_b_readiness_state": gb_rep.get("readiness_state"),
        "gate_b_staged_validation_record_present": bool(vr),
        "blockers_before_gate_a_live_micro": sorted(set(blockers_gate_a_live_micro)),
        "blockers_before_gate_b_live_execution": sorted(set(blockers_gate_b_live)),
        "truth_notes_not_actionable_blockers": [
            "Staged Gate B micro-validation does not prove authenticated venue orders, fills, or partials — "
            "run trading_ai.runtime_proof.live_execution_validation.run_gate_b_live_micro_validation for that.",
            "Absence of gate_b_live_execution_validation.json is expected until a successful Gate B live-micro run.",
        ],
        "blockers_before_first_few_trades": sorted(set(blockers_first_few_trades)),
        "honesty": (
            "This file is derived from env + runtime files; it does not grant execution permission. "
            "Staged Gate B proof does not substitute for live venue proof."
        ),
    }


def write_live_enablement_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_live_enablement_truth(runtime_root=root)
    (ctrl / "live_enablement_truth.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "LIVE ENABLEMENT TRUTH (no secrets stored here)",
        f"Generated: {payload['generated_at']}",
        f"Runtime root: {payload['runtime_root']} (exists: {payload['runtime_root_path_exists']})",
        "",
        "Before Gate A live micro-validation, resolve:",
        *([f"  - {x}" for x in payload["blockers_before_gate_a_live_micro"]] or ["  (none listed — still verify checklist)"]),
        "",
        "Before real Gate B live execution, resolve:",
        *([f"  - {x}" for x in payload["blockers_before_gate_b_live_execution"]] or ["  (no env/operator blockers listed)"]),
        "",
        "Truth notes (always read):",
        *([f"  * {x}" for x in payload.get("truth_notes_not_actionable_blockers") or []]),
        "",
        "Before first few trades (Gate A scope), resolve:",
        *([f"  - {x}" for x in payload["blockers_before_first_few_trades"]] or ["  (none listed — still verify governance)"]),
        "",
        payload["honesty"],
    ]
    (ctrl / "live_enablement_truth.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
