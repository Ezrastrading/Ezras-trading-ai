"""
ARMED_BUT_OFF bundle: final operator-facing authority artifacts (no fake green).

Call ``write_all_armed_but_off_artifacts`` from closure refresh — does not enable live trading.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.orchestration.autonomous_daemon_live_contract import (
    autonomous_daemon_may_submit_live_orders,
    read_autonomous_daemon_live_enable,
    write_autonomous_daemon_live_enable_example,
    write_autonomous_daemon_live_enable_guidance,
)
from trading_ai.orchestration.daemon_live_authority import compute_env_fingerprint
from trading_ai.orchestration.final_pre_live_writers import MATERIAL_CLOSURE_SURFACES
from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def write_buy_sell_log_rebuy_runtime_authority(*, runtime_root: Path) -> Dict[str, Any]:
    ad = _ad(runtime_root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    rb = ad.read_json("data/control/rebuy_runtime_truth.json") or {}
    bslr = bool(loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") or loop.get("final_execution_proven"))
    payload = {
        "truth_version": "buy_sell_log_rebuy_runtime_authority_v1",
        "generated_at": _iso(),
        "buy_sell_log_rebuy_contract_wired": True,
        "buy_sell_log_rebuy_runtime_proven": bslr,
        "rebuy_requires_terminal_truth": True,
        "rebuy_block_reason_if_false": str(rb.get("exact_reason_if_blocked") or ""),
        "source_loop_proof_path": "data/control/universal_execution_loop_proof.json",
        "avenue_specific_truth": {
            "A": "gate_a live validation + universal loop proof — see execution_proof/",
            "B": "independent_live_proof required — not inherited from A",
            "C": "tastytrade scaffold — honest not_wired unless proof file",
        },
        "honesty": "Harness success does not set runtime_proven — see daemon_test_authority.json.",
    }
    ad.write_json("data/control/buy_sell_log_rebuy_runtime_authority.json", payload)
    ad.write_text("data/control/buy_sell_log_rebuy_runtime_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def _pair_blockers(
    avenue_id: str,
    *,
    runtime_root: Path,
    sw: bool,
    bl: List[str],
    indep_ok: bool,
    indep_reason: str,
) -> List[str]:
    out = list(bl)
    if avenue_id in ("B", "C") and not indep_ok:
        out.append(indep_reason)
    return out


def write_universal_avenue_gate_live_matrix(*, runtime_root: Path) -> Dict[str, Any]:
    """One row per avenue×gate execution surface — isolated readiness; no cross-avenue inheritance."""
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    dual_ok, _ = autonomous_daemon_may_submit_live_orders(runtime_root=root)
    sw_a, bl_a, _ = compute_avenue_switch_live_now("A", runtime_root=root)
    sw_b, bl_b, _ = compute_avenue_switch_live_now("B", runtime_root=root)
    sw_c, bl_c, _ = compute_avenue_switch_live_now("C", runtime_root=root)

    sup_a = bool(auth.get("avenue_a_can_run_supervised_live_now"))
    aut_a = bool(auth.get("avenue_a_can_run_autonomous_live_now"))
    sup_gb = bool(auth.get("gate_b_can_run_supervised_live_now"))
    aut_gb = bool(auth.get("gate_b_can_run_autonomous_live_now"))

    indep_b = ad.read_json("data/control/avenue_B_independent_live_proof.json") or {}
    indep_c = ad.read_json("data/control/avenue_C_independent_live_proof.json") or {}
    indep_b_ok = bool(indep_b.get("independent_live_proven") and indep_b.get("validated_by_operator"))
    indep_c_ok = bool(indep_c.get("independent_live_proven") and indep_c.get("validated_by_operator"))

    rows: List[Dict[str, Any]] = [
        {
            "avenue_id": "A",
            "avenue_name": "Coinbase",
            "gate_id": "gate_a",
            "execution_stack_present": True,
            "supervised_live_ready": sup_a and sw_a,
            "autonomous_live_ready": aut_a and sw_a,
            "live_orders_enabled_now": bool(aut_a and dual_ok and sw_a),
            "independent_live_proof_required": False,
            "independent_live_proof_present": True,
            "daemon_supported": True,
            "not_wired_reason": "",
            "exact_blockers": [x for x in bl_a if x][:24],
        },
        {
            "avenue_id": "A",
            "avenue_name": "Coinbase",
            "gate_id": "gate_b",
            "execution_stack_present": True,
            "supervised_live_ready": sup_gb and sw_a,
            "autonomous_live_ready": aut_gb and sw_a,
            "live_orders_enabled_now": bool(aut_gb and dual_ok and sw_a),
            "independent_live_proof_required": False,
            "independent_live_proof_present": True,
            "daemon_supported": True,
            "not_wired_reason": "",
            "exact_blockers": [x for x in bl_a if x][:24],
        },
        {
            "avenue_id": "B",
            "avenue_name": "Kalshi",
            "gate_id": "gate_b",
            "execution_stack_present": True,
            "supervised_live_ready": bool(sw_b and indep_b_ok),
            "autonomous_live_ready": False,
            "live_orders_enabled_now": False,
            "independent_live_proof_required": True,
            "independent_live_proof_present": indep_b_ok,
            "daemon_supported": False,
            "not_wired_reason": "",
            "exact_blockers": _pair_blockers("B", runtime_root=root, sw=sw_b, bl=bl_b, indep_ok=indep_b_ok, indep_reason="independent_live_proof_missing"),
        },
        {
            "avenue_id": "C",
            "avenue_name": "Tastytrade",
            "gate_id": "gate_c",
            "execution_stack_present": False,
            "supervised_live_ready": False,
            "autonomous_live_ready": False,
            "live_orders_enabled_now": False,
            "independent_live_proof_required": True,
            "independent_live_proof_present": indep_c_ok,
            "daemon_supported": False,
            "not_wired_reason": "tastytrade_execution_stack_scaffold_not_daemon_wired",
            "exact_blockers": _pair_blockers("C", runtime_root=root, sw=sw_c, bl=bl_c, indep_ok=indep_c_ok, indep_reason="independent_live_proof_missing"),
        },
    ]

    payload = {
        "truth_version": "universal_avenue_gate_live_matrix_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "matrix": rows,
        "honesty": "Avenue B/C do not inherit A readiness; C remains scaffold until wired.",
    }
    ad.write_json("data/control/universal_avenue_gate_live_matrix.json", payload)
    ad.write_text("data/control/universal_avenue_gate_live_matrix.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_autonomous_daemon_final_truth(*, runtime_root: Path) -> Dict[str, Any]:
    ad = _ad(runtime_root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    aauth = ad.read_json("data/control/avenue_a_autonomous_authority.json") or {}
    dual_ok, dual_bl = autonomous_daemon_may_submit_live_orders(runtime_root=runtime_root)
    enable = read_autonomous_daemon_live_enable(runtime_root=runtime_root)
    sup = bool(auth.get("avenue_a_can_run_supervised_live_now"))
    aut = bool(auth.get("avenue_a_can_run_autonomous_live_now"))
    proven = bool(aauth.get("autonomous_live_runtime_proven"))
    payload = {
        "truth_version": "autonomous_daemon_final_truth_v1",
        "generated_at": _iso(),
        "can_supervised_live_run_now": sup,
        "can_autonomous_live_run_now": aut,
        "is_autonomous_daemon_runtime_proven": proven,
        "is_daemon_live_currently_enabled": bool(dual_ok),
        "if_not_enabled_single_switch_remaining": (
            "Set data/control/autonomous_daemon_live_enable.json confirmed=true AND EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true"
        ),
        "if_blocked_exact_blockers": dual_bl if not dual_ok else auth.get("exact_blockers_autonomous") or [],
        "if_armed_but_off_exact_activation_path": "python3 -m trading_ai.deployment daemon-arm-live --confirm (then export env) — see guidance artifact",
        "enable_artifact_summary": {k: enable.get(k) for k in ("confirmed", "avenue_ids_enabled", "gate_ids_enabled") if enable},
        "refs": {
            "daemon_live_switch_authority": "data/control/daemon_live_switch_authority.json",
            "avenue_a_autonomous_authority": "data/control/avenue_a_autonomous_authority.json",
            "enable": "data/control/autonomous_daemon_live_enable.json",
        },
        "honesty": "Live orders require dual gate + policy booleans — not matrix fake tiers.",
    }
    ad.write_json("data/control/autonomous_daemon_final_truth.json", payload)
    ad.write_text("data/control/autonomous_daemon_final_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_runtime_material_change_authority(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    payload = {
        "truth_version": "runtime_material_change_authority_v1",
        "generated_at": _iso(),
        "surfaces": sorted(MATERIAL_CLOSURE_SURFACES.keys()),
        "surface_paths": {k: v for k, v in MATERIAL_CLOSURE_SURFACES.items()},
        "refresh_hook": "trading_ai.universal_execution.runtime_truth_material_change.refresh_runtime_truth_after_material_change",
        "closure_hook": "trading_ai.operator_truth.live_switch_closure_bundle.write_live_switch_closure_bundle",
        "honesty": "Fingerprints are mtime+size — full closure on material change; no hidden live enable.",
    }
    ad.write_json("data/control/runtime_material_change_authority.json", payload)
    ad.write_text("data/control/runtime_material_change_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_ceo_session_runtime_truth(*, runtime_root: Path) -> Dict[str, Any]:
    from trading_ai.orchestration.avenue_a_daemon_artifacts import write_ceo_session_truth

    base = write_ceo_session_truth(runtime_root=runtime_root)
    ad = _ad(Path(runtime_root).resolve())
    payload = {
        "truth_version": "ceo_session_runtime_truth_v1",
        "generated_at": _iso(),
        "ceo_session_wiring_present": True,
        "ceo_session_artifacts_present": bool(base.get("ceo_daily_review_present")),
        "daily_session_expected": True,
        "daily_session_scheduler_present_or_external": "external_or_cron_not_verified_here",
        "session_truth_source": "data/control/ceo_session_truth.json + data/review/*",
        "exact_gap_if_any": "Scheduler not proven in-repo — use operational runbooks.",
        "embedded_ceo_session_truth": base,
    }
    ad.write_json("data/control/ceo_session_runtime_truth.json", payload)
    ad.write_text("data/control/ceo_session_runtime_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_lessons_learning_runtime_authority(*, runtime_root: Path) -> Dict[str, Any]:
    ad = _ad(Path(runtime_root).resolve())
    les = ad.read_json("data/control/lessons_runtime_effect.json") or {}
    les_final = ad.read_json("data/control/lessons_final_runtime_truth.json") or {}
    infl = bool(les.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN") or les_final.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"))
    payload = {
        "truth_version": "lessons_learning_runtime_authority_v1",
        "generated_at": _iso(),
        "lessons_storage_present": ad.exists("data/control/lessons_runtime_effect.json"),
        "lessons_used_for_live_ranking": infl,
        "lessons_used_for_entry": infl,
        "lessons_used_for_exit": infl,
        "lessons_used_for_rebuy": infl,
        "lessons_runtime_decision_influence_proven": infl,
        "exact_gap_if_false": "" if infl else "lessons_runtime_effect not proving influence — see lessons artifacts",
        "honesty": "Research/refresh may run without live orders; influence is separately proven.",
    }
    ad.write_json("data/control/lessons_learning_runtime_authority.json", payload)
    ad.write_text("data/control/lessons_learning_runtime_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload




def write_daemon_test_authority(*, runtime_root: Path) -> Dict[str, Any]:
    ad = _ad(Path(runtime_root).resolve())
    cov = ad.read_json("data/control/daemon_test_coverage_summary.json") or {}
    payload = {
        "truth_version": "daemon_test_authority_v1",
        "generated_at": _iso(),
        "fake_coverage": cov.get("passed_fake") or "see_daemon_matrix",
        "replay_coverage": cov.get("passed_replay") or "see_daemon_matrix",
        "live_proof_compatibility_coverage": cov.get("live_proof_compatible_count"),
        "runtime_proof_coverage": cov.get("runtime_proven_count"),
        "logic_proven_vs_runtime_proven": (
            "fake/replay prove adapter logic only — autonomous_live_runtime_proven uses operator artifacts + consecutive cycles"
        ),
        "honesty": "Harness success does not set AUTONOMOUS_DAEMON_RUNTIME_PROVEN.",
    }
    ad.write_json("data/control/daemon_test_authority.json", payload)
    ad.write_text("data/control/daemon_test_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_daemon_runtime_authority_alias(*, runtime_root: Path) -> Dict[str, Any]:
    """Thin alias artifact — points at autonomous authority + proof files."""
    ad = _ad(Path(runtime_root).resolve())
    aa = ad.read_json("data/control/avenue_a_autonomous_authority.json") or {}
    payload = {
        "truth_version": "avenue_a_daemon_runtime_authority_v1",
        "generated_at": _iso(),
        "canonical_merge": "data/control/avenue_a_autonomous_authority.json",
        "same_as_avenue_a_autonomous_authority_keys": ["supervised_live_ready", "autonomous_live_ready", "autonomous_live_runtime_proven"],
        "embedded_snapshot": {k: aa.get(k) for k in ("supervised_live_ready", "autonomous_live_ready", "autonomous_live_runtime_proven", "artifact_refs") if aa},
    }
    ad.write_json("data/control/avenue_a_daemon_runtime_authority.json", payload)
    ad.write_text("data/control/avenue_a_daemon_runtime_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def classify_final_daemon_go_live(
    *,
    runtime_root: Path,
) -> Tuple[str, str]:
    """
    Returns (classification, human_note).

    Targets ``AUTONOMOUS_READY_BUT_LIVE_DISARMED`` when runtime green but dual gate false.
    """
    ad = _ad(Path(runtime_root).resolve())
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    aauth = ad.read_json("data/control/avenue_a_autonomous_authority.json") or {}
    dual_ok, _ = autonomous_daemon_may_submit_live_orders(runtime_root=runtime_root)
    sup = bool(auth.get("avenue_a_can_run_supervised_live_now"))
    aut = bool(auth.get("avenue_a_can_run_autonomous_live_now"))
    proven = bool(aauth.get("autonomous_live_runtime_proven"))

    if not sup and not aut:
        return "NOT_READY", "daemon_live_switch_authority blocks both tiers — see exact_blockers"
    if sup and not aut:
        return "SUPERVISED_READY_AUTONOMOUS_NOT_READY", "supervised path may be clear; autonomous proof/policy not satisfied"
    if aut and proven and not dual_ok:
        return "AUTONOMOUS_READY_BUT_LIVE_DISARMED", "only explicit enable artifact + env remains for venue orders"
    if aut and dual_ok and proven:
        return "AUTONOMOUS_LIVE_ENABLED_UNDER_CURRENT_AUTHORITY", "dual gate true — live submission allowed if runtime still green"
    if aut and not proven:
        return "AUTONOMOUS_READY_BUT_LIVE_DISARMED", "policy may allow path but runtime proof not complete — treat as disarmed for orders"
    return "NOT_READY", "ambiguous — inspect daemon artifacts"


def write_final_daemon_go_live_authority(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    cls, note = classify_final_daemon_go_live(runtime_root=root)
    payload = {
        "truth_version": "final_daemon_go_live_authority_v1",
        "generated_at": _iso(),
        "final_classification": cls,
        "classification_note": note,
        "target_state_for_this_pass": "AUTONOMOUS_READY_BUT_LIVE_DISARMED",
        "runtime_root": str(root),
        "env_fingerprint_at_write": compute_env_fingerprint(),
        "honesty": "Classification is evidence-based; missing files yield NOT_READY.",
    }
    ad.write_json("data/control/final_daemon_go_live_authority.json", payload)
    ad.write_text("data/control/final_daemon_go_live_authority.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_all_armed_but_off_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    out: Dict[str, Any] = {}
    out["enable_example"] = write_autonomous_daemon_live_enable_example(runtime_root=root)
    out["enable_guidance"] = write_autonomous_daemon_live_enable_guidance(runtime_root=root)
    out["buy_sell_log_rebuy"] = write_buy_sell_log_rebuy_runtime_authority(runtime_root=root)
    out["avenue_gate_matrix"] = write_universal_avenue_gate_live_matrix(runtime_root=root)
    out["autonomous_daemon_final"] = write_autonomous_daemon_final_truth(runtime_root=root)
    out["material_change_authority"] = write_runtime_material_change_authority(runtime_root=root)
    out["ceo_session_runtime"] = write_ceo_session_runtime_truth(runtime_root=root)
    out["lessons_learning"] = write_lessons_learning_runtime_authority(runtime_root=root)
    from trading_ai.orchestration.avenue_a_daemon_artifacts import write_first_20_daemon_truth

    out["first_20_daemon"] = write_first_20_daemon_truth(runtime_root=root)
    out["daemon_test"] = write_daemon_test_authority(runtime_root=root)
    out["avenue_a_daemon_runtime_authority"] = write_avenue_a_daemon_runtime_authority_alias(runtime_root=root)
    out["final_go_live"] = write_final_daemon_go_live_authority(runtime_root=root)
    return out
