"""
Authoritative Avenue A daemon / rebuy / supervision / failure-catalog artifacts — composed from runtime truth.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_rebuy_runtime_truth(*, runtime_root: Path) -> Dict[str, Any]:
    """``data/control/rebuy_runtime_truth.json`` — rebuy allowed only when policy + loop proof align."""
    from trading_ai.universal_execution.rebuy_policy import can_open_next_trade_after

    ad = LocalStorageAdapter(runtime_root=runtime_root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    ls = loop.get("lifecycle_stages") or {}
    prior = {
        "final_execution_proven": loop.get("final_execution_proven"),
        "terminal_honest_state": None,
        "entry_fill_confirmed": ls.get("entry_fill_confirmed"),
        "exit_fill_confirmed": ls.get("exit_fill_confirmed"),
        "pnl_verified": ls.get("pnl_verified"),
        "local_write_ok": ls.get("local_write_ok"),
    }
    ok, why = can_open_next_trade_after(prior)
    block_class = "none"
    if not ok:
        lw = str(why).lower()
        if "duplicate" in lw:
            block_class = "duplicate_or_policy"
        elif "governance" in lw or "halt" in lw:
            block_class = "governance_or_adaptive"
        elif "exit" in lw or "fill" in lw:
            block_class = "exit_or_fill_truth"
        elif "logging" in lw or "local" in lw:
            block_class = "logging"
        else:
            block_class = "other"

    payload = {
        "truth_version": "rebuy_runtime_truth_v1",
        "generated_at": _iso(),
        "rebuy_allowed_now": bool(ok),
        "exact_reason_if_blocked": "" if ok else str(why),
        "last_block_class": block_class,
        "source_loop_proof": "data/control/universal_execution_loop_proof.json",
        "honesty": "Derived from universal loop proof + rebuy_policy — not tick-only success.",
    }
    ad.write_json("data/control/rebuy_runtime_truth.json", payload)
    ad.write_text("data/control/rebuy_runtime_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def build_failure_model_catalog() -> Dict[str, Any]:
    """Static severities — runtime snapshot merged by daemon/runner."""
    types: Dict[str, Any] = {
        "venue_reject": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Inspect venue + execution_proof; refresh runtime artifacts.",
        },
        "fill_truth_missing": {
            "severity": "critical",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Halt entries until fills reconciled; check Coinbase API + ledger.",
        },
        "duplicate_guard_block": {
            "severity": "medium",
            "stop_immediately": False,
            "pause_require_review": False,
            "retry_allowed": True,
            "next_action": "Wait duplicate window or resolve inflight trade.",
        },
        "governance_block": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Review joint_review + governance logs.",
        },
        "adaptive_brake": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Review adaptive_live_proof + operating mode.",
        },
        "databank_failure": {
            "severity": "critical",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Fix local trade store / federation before live entries.",
        },
        "remote_sync_failure": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Fix Supabase sync when remote is required.",
        },
        "review_packet_failure": {
            "severity": "medium",
            "stop_immediately": False,
            "pause_require_review": True,
            "retry_allowed": True,
            "next_action": "Re-run review cycle or fix ReviewStorage.",
        },
        "unresolved_inflight": {
            "severity": "critical",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Resolve prior trade before new entry.",
        },
        "repeated_failure_signature": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Inspect runtime_runner_failures.jsonl pattern.",
        },
        "expectancy_or_drawdown_stop": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Review adaptive OS + gate-scoped PnL.",
        },
        "kill_switch": {
            "severity": "critical",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Operator clears kill switch when safe.",
        },
        "stale_operator_confirmation": {
            "severity": "high",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Refresh operator_live_confirmation.json.",
        },
        "stale_go_no_go": {
            "severity": "medium",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Re-run prelive go_no_go writer.",
        },
        "stale_execution_mirror": {
            "severity": "medium",
            "stop_immediately": True,
            "pause_require_review": True,
            "retry_allowed": False,
            "next_action": "Run execution_mirror prelive.",
        },
        "stale_ceo_session": {
            "severity": "low",
            "stop_immediately": False,
            "pause_require_review": True,
            "retry_allowed": True,
            "next_action": "Run daily CEO / review cycle if policy requires freshness.",
        },
    }
    return {"truth_version": "runtime_runner_failure_model_v1", "failure_types": types, "generated_at": _iso()}


def write_runtime_runner_failure_model(*, runtime_root: Path, runtime_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    base = build_failure_model_catalog()
    if runtime_snapshot:
        base["last_runtime_snapshot"] = runtime_snapshot
    ad.write_json("data/control/runtime_runner_failure_model.json", base)
    return base


def write_minimal_supervision_contract(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_daemon_mode

    mode = avenue_a_daemon_mode()
    payload = {
        "truth_version": "minimal_supervision_contract_v1",
        "generated_at": _iso(),
        "operator_must_still_watch": [
            "Kill switch + failsafe + adaptive brake alerts",
            "data/control/universal_execution_loop_proof.json and execution_proof/* after each cycle",
            "runtime_runner_health.json + avenue_a_daemon_live_truth.json",
        ],
        "operator_does_not_need": [
            "Per-trade manual approval once autonomous_live is runtime-proven AND avenue_a_autonomous_live_ack.json is in force",
        ],
        "intervention_required_when": [
            "kill_switch or authoritative global halt",
            "FINAL_EXECUTION_PROVEN false with partial failure",
            "AUTONOMOUS_DAEMON_RUNTIME_PROVEN false while attempting autonomous_live",
        ],
        "tonight_effective_daemon_mode_declared": mode,
        "honesty": "This contract does not grant autonomy — runtime proof booleans do.",
    }
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_json("data/control/minimal_supervision_contract.json", payload)
    ad.write_text("data/control/minimal_supervision_contract.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_ceo_session_truth(*, runtime_root: Path) -> Dict[str, Any]:
    """Honest CEO freshness — does not claim automation without execution evidence."""
    root = Path(runtime_root).resolve()
    rev = root / "data" / "review"
    ceo_json = rev / "ceo_daily_review.json"
    ratio_json = rev / "daily_ratio_review.json"
    payload = {
        "truth_version": "ceo_session_truth_v1",
        "generated_at": _iso(),
        "ceo_daily_review_path": str(ceo_json),
        "ceo_daily_review_present": ceo_json.is_file(),
        "daily_ratio_review_path": str(ratio_json),
        "daily_ratio_review_present": ratio_json.is_file(),
        "ceo_automation_claim": False,
        "honesty": "Daily CEO pipelines exist in intelligence/shark; freshness requires real scheduled runs — not implied by this file.",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/ceo_session_truth.json", payload)
    ad.write_text("data/control/ceo_session_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_first_20_daemon_truth(*, runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    f20_pass = ad.read_json("data/control/first_20_pass_decision.json") or {}
    f20_final = ad.read_json("data/control/first_20_final_truth.json") or {}
    require = (os.environ.get("EZRAS_FIRST_20_REQUIRED_FOR_LIVE") or "").strip().lower() in ("1", "true", "yes")
    blocks = bool(require) and not bool(f20_pass.get("passed"))
    ready = bool(f20_final.get("FIRST_20_READY_FOR_NEXT_PHASE"))
    payload = {
        "truth_version": "first_20_daemon_truth_v2",
        "generated_at": _iso(),
        "first_20_required_for_live_by_policy": require,
        "first_20_ready_for_next_phase": ready,
        "first_20_blocks_supervised_live": blocks,
        "first_20_blocks_autonomous_live": blocks,
        "EZRAS_FIRST_20_REQUIRED_FOR_LIVE": require,
        "first_20_blocks_autonomous_daemon": blocks,
        "first_20_blocks_live_switch": blocks,
        "first_20_typically_affects": "caution_and_sizing_under_gate_a_scope",
        "exact_reason_if_blocking": "first_20_pass_false_under_required_env" if blocks else "",
        "exact_blocker_if_any": "first_20_pass_false_under_required_env" if blocks else "",
        "honesty": "Not mandatory unless EZRAS_FIRST_20_REQUIRED_FOR_LIVE is set.",
    }
    ad.write_json("data/control/first_20_daemon_truth.json", payload)
    ad.write_text("data/control/first_20_daemon_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_switch_booleans(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
    from trading_ai.orchestration import runtime_runner as rr
    from trading_ai.orchestration.avenue_a_daemon_policy import (
        avenue_a_autonomous_runtime_proven,
        avenue_a_supervised_runtime_allowed,
    )

    root = Path(runtime_root).resolve()
    sw, bl, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    sup_ok, sup_why = avenue_a_supervised_runtime_allowed(runtime_root=root)
    aut_ok, aut_why = avenue_a_autonomous_runtime_proven(runtime_root=root)
    payload = {
        "truth_version": "avenue_a_switch_booleans_v1",
        "generated_at": _iso(),
        "AVENUE_A_CAN_SWITCH_LIVE_NOW": bool(sw),
        "AVENUE_A_CAN_RUN_SUPERVISED_LIVE_NOW": bool(sw and sup_ok),
        "AVENUE_A_CAN_RUN_AUTONOMOUS_LIVE_NOW": bool(sw and sup_ok and aut_ok),
        "switch_live_blockers": list(bl),
        "supervised_blocker_if_false": sup_why,
        "autonomous_blocker_if_false": aut_why,
        "CONTINUOUS_DAEMON_RUNTIME_PROVEN": rr.evaluate_continuous_daemon_runtime_proven(runtime_root=root),
        "honesty": "autonomous requires daemon verification JSON + consecutive OK cycles — see avenue_a_daemon_live_truth.json",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/avenue_a_live_switch_booleans.json", payload)
    return payload


def write_avenue_a_daemon_sequences(*, runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    safe = {
        "truth_version": "avenue_a_safe_go_live_sequence_v1",
        "steps": [
            "Confirm system_execution_lock + operator_live_confirmation.json",
            "Run execution mirror + mock harness + go_no_go",
            "Run python -m trading_ai.deployment refresh-runtime-artifacts --force",
            "Supervised gate-a live validation or daemon supervised_live before autonomous",
        ],
        "generated_at": _iso(),
    }
    act = {
        "truth_version": "avenue_a_daemon_activation_sequence_v1",
        "steps": [
            "Create data/control/avenue_a_autonomous_live_ack.json with confirmed true",
            "Export EZRAS_AVENUE_A_DAEMON_MODE=supervised_live or autonomous_live",
            "python -m trading_ai.deployment avenue-a-daemon-start",
        ],
        "generated_at": _iso(),
    }
    ad.write_json("data/control/avenue_a_safe_go_live_sequence.json", safe)
    ad.write_text("data/control/avenue_a_safe_go_live_sequence.txt", json.dumps(safe, indent=2) + "\n")
    ad.write_json("data/control/avenue_a_daemon_activation_sequence.json", act)
    ad.write_text("data/control/avenue_a_daemon_activation_sequence.txt", json.dumps(act, indent=2) + "\n")
    return {"safe": safe, "activation": act}


def write_avenue_a_daemon_blockers(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
    from trading_ai.orchestration.avenue_a_daemon_policy import (
        avenue_a_autonomous_runtime_proven,
        avenue_a_supervised_runtime_allowed,
    )

    root = Path(runtime_root).resolve()
    sw, bl, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    sup_ok, sup_why = avenue_a_supervised_runtime_allowed(runtime_root=root)
    aut_ok, aut_why = avenue_a_autonomous_runtime_proven(runtime_root=root)
    critical: List[str] = []
    if not sw:
        critical.extend(bl)
    if not sup_ok:
        critical.append(f"supervised:{sup_why}")
    advisory: List[str] = []
    if not aut_ok:
        advisory.append(f"autonomous_not_proven:{aut_why}")
    payload = {
        "truth_version": "avenue_a_daemon_blockers_v1",
        "generated_at": _iso(),
        "critical_blockers": critical,
        "advisory_for_autonomous_only": advisory,
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/avenue_a_daemon_blockers.json", payload)
    ad.write_text("data/control/avenue_a_daemon_blockers.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_all_avenue_a_daemon_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    out: Dict[str, Any] = {}
    out["rebuy"] = write_rebuy_runtime_truth(runtime_root=root)
    out["failure_model"] = write_runtime_runner_failure_model(runtime_root=root)
    out["supervision"] = write_minimal_supervision_contract(runtime_root=root)
    out["ceo"] = write_ceo_session_truth(runtime_root=root)
    out["first_20_daemon"] = write_first_20_daemon_truth(runtime_root=root)
    out["switch_booleans"] = write_avenue_a_switch_booleans(runtime_root=root)
    out["sequences"] = write_avenue_a_daemon_sequences(runtime_root=root)
    out["daemon_blockers"] = write_avenue_a_daemon_blockers(runtime_root=root)
    return out
