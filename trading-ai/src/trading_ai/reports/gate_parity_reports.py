"""Gate A vs Gate B runtime parity matrix and full-system lock audit text/json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report


def build_runtime_parity_matrix(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    gb = gate_b_live_status_report()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gate_a": {
            "execution_venue": "coinbase_nte_gate_a",
            "ledger": "trade_ledger.jsonl_with_gate_a",
            "capital_truth": "quote_balances_coinbase_schema",
            "readiness_typical": "first_20_gate_a_scope",
        },
        "gate_b": {
            "execution_venue": "coinbase_spot_row_gainers_gate_b",
            "ledger": "trade_ledger.jsonl gate_id=gate_b (staged harness + live Coinbase submit path)",
            "capital_truth": "same Coinbase quote schema when routed via Coinbase; Kalshi legs use explicit non-Coinbase ledger semantics",
            "readiness": gb.get("production_state"),
            "readiness_state": gb.get("readiness_state"),
            "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
            "gate_b_live_micro_proven": gb.get("gate_b_live_micro_proven"),
            "gate_b_staged_micro_proven": gb.get("gate_b_staged_micro_proven"),
            "scanner_engine": "GateBMomentumEngine",
            "honesty": (
                "Staged micro proves harness + duplicate/failsafe without venue HTTP; "
                "live-micro proven only via execution_proof/gate_b_live_execution_validation.json "
                "(authenticated Coinbase round-trip), never via staged files alone."
            ),
        },
        "parity": {
            "same_class_of_artifacts": [
                "failsafe_preflight",
                "governance_order_gate",
                "trade_ledger_line",
                "execution_mirror_optional",
                "intelligence_hooks_submit_and_post_trade",
            ],
            "not_identical": [
                "Gate A: NTE round-trip proof file vs Gate B: staged harness + separate gate_b_live_execution_validation.json for live venue proof",
                "Gate A: validation_product_resolution vs Gate B: gate_b_validation.json staged fields + live_venue_micro_validation_pass",
            ],
        },
    }


def write_gate_a_gate_b_runtime_parity(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_runtime_parity_matrix(runtime_root=root)
    (ctrl / "gate_a_gate_b_runtime_parity.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"generated_at: {payload['generated_at']}",
        "",
        "Gate A: Coinbase / NTE — capital truth on quote balances.",
        "Gate B: Coinbase spot-row gainers — gate_b_validation.json + micro proof; Kalshi orders are out of scope here.",
        "",
        "Parity: both pass governance + failsafe where applicable; artifacts differ by venue honestly.",
    ]
    (ctrl / "gate_a_gate_b_runtime_parity.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def write_full_system_lock_audit(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "locks": [
            "failsafe_guard_duplicate_window_canonical",
            "live_order_guard_choke_point",
            "system_execution_lock_file",
            "gate_b_live_status_state_machine",
            "intelligence_hooks_non_blocking",
        ],
        "not_locked_until_live": [
            "venue_fill_slippage_truth",
            "host_git_deploy_truth",
        ],
    }
    (ctrl / "full_system_lock_audit.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (ctrl / "full_system_lock_audit.txt").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_gate_a_gate_b_proof_parity_matrix(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Exact proof-field matrix for audits (static + pointers to runtime files)."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    gb_proof = root / "data" / "control" / "gate_b_micro_validation_proof.json"
    gb_val = root / "data" / "control" / "gate_b_validation.json"
    exec_proof = root / "execution_proof" / "live_execution_validation.json"
    gb_live_exec = root / "execution_proof" / "gate_b_live_execution_validation.json"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "gate_a": {
            "primary_proof_artifacts": [
                "execution_proof/live_execution_validation.json",
                "data/control/validation_product_resolution_report.json",
                "data/control/quote_capital_truth.json",
                "data/ledger/trade_ledger.jsonl",
            ],
            "typical_boolean_fields": [
                "READY_FOR_FIRST_20",
                "FINAL_EXECUTION_PROVEN",
                "execution_success",
                "coinbase_order_verified",
            ],
            "execution_proof_present": exec_proof.is_file(),
        },
        "gate_b": {
            "primary_proof_artifacts": [
                "data/control/gate_b_micro_validation_proof.json",
                "data/control/gate_b_validation.json",
                "execution_proof/gate_b_live_execution_validation.json (live venue micro; not staged)",
                "data/deployment/live_validation_runs/gate_b_micro_validation_*.json",
                "data/ledger/trade_ledger.jsonl (gate_id=gate_b)",
            ],
            "typical_fields": [
                "all_passed",
                "micro_validation_pass",
                "validation_mode",
                "live_venue_micro_validation_pass",
                "FINAL_EXECUTION_PROVEN",
                "gate_b_order_verified",
                "harness.all_passed",
                "duplicate_trade_guard.passed",
            ],
            "micro_proof_present": gb_proof.is_file(),
            "validation_json_present": gb_val.is_file(),
            "live_execution_proof_present": gb_live_exec.is_file(),
        },
        "equivalent_in_intent": [
            "Append-only trade ledger line with gate_id",
            "Operator/system execution lock consulted on live paths",
            "Control JSON under data/control for audit replay",
        ],
        "intentionally_different": [
            "Gate A proves venue round-trip + databank sync when run with capital",
            "Gate B: staged harness without HTTP; live venue proof via gate_b_live_execution_validation.json",
        ],
        "still_missing_until_live": [
            "Gate A: may be missing execution_proof until operator runs live micro-validation",
            "Gate B: may be missing gate_b_live_execution_validation.json until operator runs Gate B live micro",
        ],
        "gate_a_requires_that_gate_b_staged_does_not": [
            "Venue-authenticated round-trip with real buy/sell IDs and fill reconciliation",
            "Supabase / databank / governance pipeline proof tied to that trade_id",
            "READY_FOR_FIRST_20 / deployment checklist coupling for first-few-trades scope",
        ],
        "gate_b_staged_proves_instead_of_live": [
            "Deterministic scenario harness (breakouts, exits, duplicate guard) without HTTP",
            "Auto gate_b_validation.json from harness + failsafe duplicate check",
        ],
        "gate_b_live_micro_proves_instead_of_staged": [
            "Authenticated Coinbase buy+sell IDs + fill truth + pipeline booleans in gate_b_live_execution_validation.json",
        ],
        "common_to_both": [
            "trade_ledger.jsonl lines with gate_id",
            "system_execution_lock consulted on live submit paths",
            "Intelligence hooks callable from validation flows",
        ],
        "staged_vs_live_labels": {
            "gate_a_live_proof_file": "execution_proof/live_execution_validation.json from run_single_live_execution_validation",
            "gate_b_staged_proof_files": "data/control/gate_b_micro_validation_proof.json + gate_b_validation.json (validation_mode staged/mock)",
            "gate_b_live_micro_proof_file": "execution_proof/gate_b_live_execution_validation.json from run_gate_b_live_micro_validation",
        },
    }


def write_gate_a_gate_b_parity_matrix(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_gate_a_gate_b_proof_parity_matrix(runtime_root=root)
    (ctrl / "gate_a_gate_b_parity_matrix.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    txt_lines = [
        f"generated_at: {payload['generated_at']}",
        "",
        "Gate A proof fields: see gate_a.primary_proof_artifacts + execution_proof JSON booleans.",
        "Gate B proof fields: gate_b_micro_validation_proof.json + gate_b_validation.json.",
        "Equivalent / different / missing: see JSON keys equivalent_in_intent, intentionally_different, still_missing_until_live.",
    ]
    (ctrl / "gate_a_gate_b_parity_matrix.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    return payload


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_final_system_lock_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    gb = gate_b_live_status_report()
    rs = str(gb.get("readiness_state") or "")

    ep = root / "execution_proof" / "live_execution_validation.json"
    ep_raw = _load_json(ep) if ep.is_file() else None
    gate_a_blockers: list = []
    if ep_raw is None:
        gate_a_blockers.append("execution_proof/live_execution_validation.json_missing")
    else:
        if ep_raw.get("FINAL_EXECUTION_PROVEN") is not True:
            gate_a_blockers.append("FINAL_EXECUTION_PROVEN_not_true_in_execution_proof")
        if ep_raw.get("coinbase_order_verified") is not True:
            gate_a_blockers.append("coinbase_order_verified_not_true_in_execution_proof")
    # Live micro proven only when the real pipeline booleans pass — never from staged/mock.
    gate_a_live_micro_proven = ep.is_file() and len(gate_a_blockers) == 0
    ready_first20 = bool(ep_raw.get("READY_FOR_FIRST_20")) if ep_raw else False

    gb_val_path = ctrl / "gate_b_validation.json"
    gb_val = _load_json(gb_val_path) if gb_val_path.is_file() else None
    gb_mode = str((gb_val or {}).get("validation_mode") or "")
    gb_staged_ok = bool(
        gb_val
        and (gb_val.get("micro_validation_pass") is True)
        and not gb_val.get("failed_validation")
        and ("staged" in gb_mode.lower() or "mock" in gb_mode.lower() or not gb_mode)
    )
    gb_live_path = root / "execution_proof" / "gate_b_live_execution_validation.json"
    gb_live_raw = _load_json(gb_live_path) if gb_live_path.is_file() else None
    gb_live_file_ok = bool(
        gb_live_raw
        and gb_live_raw.get("FINAL_EXECUTION_PROVEN") is True
        and (
            gb_live_raw.get("gate_b_order_verified") is True
            or gb_live_raw.get("coinbase_order_verified") is True
        )
    )
    gate_b_live_micro_proven = gb_live_file_ok

    gate_b_staged_micro_ready = gb_staged_ok
    gate_b_live_micro_ready = gate_b_live_micro_proven
    gate_b_blockers: list = []
    if not gate_b_staged_micro_ready:
        gate_b_blockers.append("gate_b_staged_micro_not_proven_or_failed_or_missing_gate_b_validation.json")
    if not gate_b_live_micro_proven:
        gate_b_blockers.append(
            "gate_b_live_execution_validation.json missing or FINAL_EXECUTION_PROVEN/gate_b_order_verified not true (staged does not count)"
        )
    if gb.get("gate_b_disabled_by_runtime_policy"):
        gate_b_blockers.append("runtime_policy_blocks_coinbase_execution")

    policy_blocked = bool(gb.get("gate_b_disabled_by_runtime_policy"))
    full_system_fully_locked = bool(
        gate_a_live_micro_proven
        and gate_b_staged_micro_ready
        and gate_b_live_micro_proven
        and not policy_blocked
    )
    if not gate_a_live_micro_proven:
        full_system_fully_locked = False
    if not gate_b_staged_micro_ready:
        full_system_fully_locked = False
    if not gate_b_live_micro_proven:
        full_system_fully_locked = False

    try:
        from trading_ai.control.system_execution_lock import load_system_execution_lock

        lock = load_system_execution_lock(runtime_root=root)
        production_enabled = bool(lock.get("system_locked") and lock.get("ready_for_live_execution"))
    except Exception as exc:
        lock = {}
        production_enabled = False
        gate_a_blockers.append(f"system_lock_read:{exc}")

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "semantics": {
            "code_ready": "Implementation exists in-repo (always true for shipped gates).",
            "staged_proven": "Gate B harness + gate_b_validation.json staged/mock path passed.",
            "live_micro_proven_gate_a": "execution_proof/live_execution_validation.json with FINAL_EXECUTION_PROVEN and coinbase_order_verified true.",
            "live_micro_proven_gate_b": "execution_proof/gate_b_live_execution_validation.json with FINAL_EXECUTION_PROVEN and gate_b_order_verified (or coinbase_order_verified) true — never staged-only.",
            "production_enabled": "system_execution_lock + operator env — see live_enablement_truth.json.",
        },
        "gate_a": {
            "live_micro_proven": gate_a_live_micro_proven,
            "ready_for_first_20_in_proof": ready_first20,
            "blockers": gate_a_blockers,
            "honesty": "gate_a live_micro_proven is FALSE unless execution_proof contains real live validation booleans (not staged).",
        },
        "gate_b": {
            "staged_micro_ready": gate_b_staged_micro_ready,
            "live_micro_ready": gate_b_live_micro_ready,
            "live_micro_proven": gate_b_live_micro_proven,
            "readiness_state": rs,
            "blockers": gate_b_blockers,
            "honesty": "staged_micro_ready does not imply authenticated venue execution; live_micro_ready requires gate_b_live_execution_validation.json booleans (not staged/mock).",
        },
        "system": {
            "full_system_fully_locked": full_system_fully_locked,
            "production_enabled_hint": production_enabled,
            "policy_blocked_gate_b": policy_blocked,
        },
        "deprecated_aliases": {
            "gate_a.micro_ready_means": "Same as gate_a.live_micro_proven (strict).",
        },
    }
    (ctrl / "final_system_lock_status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    txt = f"""FINAL SYSTEM LOCK STATUS (authoritative snapshot)
Generated: {payload['generated_at']}
Runtime root: {root}

Gate A — live micro proven (real Coinbase proof file): {gate_a_live_micro_proven}
  Blockers: {gate_a_blockers or ['none']}
  READY_FOR_FIRST_20 in proof: {ready_first20}

Gate B — staged micro ready (harness + gate_b_validation.json): {gate_b_staged_micro_ready}
Gate B — live micro ready (venue proof; NOT staged): {gate_b_live_micro_ready}
  Readiness state: {rs}
  Blockers: {gate_b_blockers or ['none']}

Full system fully locked (Gate A live proof + Gate B staged + Gate B live proof + no policy block): {full_system_fully_locked}

This file never marks Gate A ready from staged/mock artifacts.
Staged Gate B proof never counts as live venue proof.
See live_enablement_truth.txt for env/credential gates.
"""
    (ctrl / "final_system_lock_status.txt").write_text(txt, encoding="utf-8")
    return payload
