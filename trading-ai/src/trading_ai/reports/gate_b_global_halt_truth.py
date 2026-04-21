"""
Global halt vs Gate B switch-live authority — informational brake only (no AOS persist, no diagnosis overwrite).

``blocked_by_global_adaptive`` in contamination audit remains the raw persisted global mode truth.
This module adds whether that halt is still supported by **current** scoped production evidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.control.adaptive_operating_system import (
    load_operating_mode_config_from_env,
    load_persisted_state,
)
from trading_ai.control.adaptive_scope import default_production_pnl_only, operating_mode_state_path_for_key
from trading_ai.control.emergency_brake import evaluate_emergency_brake
from trading_ai.control.live_adaptive_integration import build_live_operating_snapshot
from trading_ai.control.operating_mode_types import OperatingMode
from trading_ai.runtime_paths import ezras_runtime_root

# Operator-created; does not clear global operating_mode_state.json.
GOVERNANCE_ACK_FILENAME = "gate_b_live_switch_governance_ack.json"
EXAMPLE_ACK_FILENAME = "gate_b_live_switch_governance_ack.example.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _gate_b_governance_ack_present(ctrl: Path) -> bool:
    p = ctrl / GOVERNANCE_ACK_FILENAME
    if not p.is_file():
        return False
    raw = _read_json(p)
    if not raw:
        return False
    scope = str(raw.get("scope") or "").lower()
    ok_scope = scope in (
        "",
        "gate_b",
        "gate_b_only",
        "gate_b_coinbase_live_only",
    )
    return bool(raw.get("operator_cleared_for_gate_b_live")) and ok_scope


def _classify_global_halt(
    *,
    persisted_global_halted: bool,
    brake_global: Any,
    brake_gate_b: Any,
    snap_g: Any,
    snap_b: Any,
) -> Tuple[str, List[str]]:
    """
    Return (primary_classification, contributing_factors) — one primary A/B/C/D.
    """
    factors: List[str] = []

    if not persisted_global_halted:
        return "NONE", ["persisted_global_mode_not_halted"]

    rec_g = brake_global.recommended_floor
    halt_floor_global = rec_g == OperatingMode.HALTED
    infra = (
        snap_g.reconciliation_failures_24h > 0
        or snap_g.databank_failures_24h >= 3
        or snap_g.governance_blocks_24h >= 8
    )
    for a in snap_g.anomaly_flags or []:
        if str(a).startswith("runtime_integrity"):
            infra = True
            break

    if infra:
        factors.append("reconciliation_databank_governance_or_integrity_flags")
        return "REAL_CURRENT_GLOBAL_RISK", factors

    ga = snap_g.gate_a_expectancy_20
    gb = snap_g.gate_b_expectancy_20
    gate_a_stress = ga is not None and ga < 0
    gate_b_ok = gb is None or gb >= 0
    halt_floor_b = brake_gate_b.recommended_floor == OperatingMode.HALTED

    if (
        gate_a_stress
        and gate_b_ok
        and not halt_floor_b
        and brake_global.triggered
        and ga is not None
    ):
        factors.append("global_series_mixes_gates_gate_a_expectancy_negative_gate_b_not")
        return "CONTAMINATED_SCOPE_MERGE", factors

    if not brake_global.triggered:
        factors.append("informational_emergency_brake_not_triggered_on_current_global_production_snapshot")
        return "STALE_PERSISTED_STATE", factors

    if brake_global.triggered and not halt_floor_global:
        factors.append("brake_triggers_but_recommended_floor_not_halted_recovery_may_be_possible")
        return "STALE_PERSISTED_STATE", factors

    if halt_floor_global and brake_global.triggered:
        factors.append("current_production_evidence_still_warrants_halt_floor")
        return "REAL_CURRENT_GLOBAL_RISK", factors

    factors.append("governance_or_ambiguous_review_recommended")
    return "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL", factors


def build_gate_b_global_halt_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    prod = default_production_pnl_only()

    global_path = operating_mode_state_path_for_key("global")
    gb_path = operating_mode_state_path_for_key("gate_b")

    pst_g = load_persisted_state("global")
    pst_b = load_persisted_state("gate_b")
    persisted_global_halted = str(pst_g.mode or "").lower() == "halted"
    persisted_gb_halted = str(pst_b.mode or "").lower() == "halted"

    cfg = load_operating_mode_config_from_env()
    snap_g = build_live_operating_snapshot(
        evaluation_scope="global",
        production_pnl_only=prod,
    )
    snap_b = build_live_operating_snapshot(
        evaluation_scope="gate_b",
        production_pnl_only=prod,
    )
    brake_g = evaluate_emergency_brake(snap_g, cfg)
    brake_b = evaluate_emergency_brake(snap_b, cfg)

    primary, factors = _classify_global_halt(
        persisted_global_halted=persisted_global_halted,
        brake_global=brake_g,
        brake_gate_b=brake_b,
        snap_g=snap_g,
        snap_b=snap_b,
    )

    ack = _gate_b_governance_ack_present(ctrl)

    halt_floor_g = brake_g.recommended_floor == OperatingMode.HALTED
    halt_floor_b = brake_b.recommended_floor == OperatingMode.HALTED

    severe_technical = (
        snap_g.reconciliation_failures_24h > 0
        or snap_g.databank_failures_24h >= 3
        or halt_floor_g
        or (brake_g.triggered and brake_g.severity >= 90)
    )

    global_authoritative_for_switch = bool(
        persisted_global_halted and (severe_technical or primary == "REAL_CURRENT_GLOBAL_RISK")
    )
    if primary == "CONTAMINATED_SCOPE_MERGE" and not ack:
        global_authoritative_for_switch = True
    if primary == "STALE_PERSISTED_STATE" and not severe_technical:
        global_authoritative_for_switch = False
    if primary == "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL":
        global_authoritative_for_switch = True

    is_stale = primary == "STALE_PERSISTED_STATE"

    operator_clearable = bool(
        persisted_global_halted
        and primary == "CONTAMINATED_SCOPE_MERGE"
        and not halt_floor_b
        and not persisted_gb_halted
        and not ack
    )

    switch_authority = "denied_global_halt_authoritative"
    if not persisted_global_halted:
        switch_authority = "allowed_no_global_halt_persisted"
    elif primary == "STALE_PERSISTED_STATE" and not severe_technical:
        switch_authority = "allowed_gate_b_scoped_current_evidence_does_not_re_halt_global_series"
    elif primary == "CONTAMINATED_SCOPE_MERGE" and ack and not halt_floor_b and not persisted_gb_halted:
        switch_authority = "allowed_gate_b_with_operator_governance_ack"
    elif primary == "CONTAMINATED_SCOPE_MERGE" and not ack:
        switch_authority = "pending_operator_governance_ack_for_gate_b_only"

    gov_blocking = bool(primary == "GOVERNANCE_REVIEW_REQUIRED_BUT_NOT_TECHNICAL") or (
        primary == "CONTAMINATED_SCOPE_MERGE" and not ack
    )

    tech_blockers: List[str] = []
    if severe_technical:
        tech_blockers.append("severe_global_technical_or_halt_floor_on_current_snapshot")
    if persisted_gb_halted:
        tech_blockers.append("gate_b_persisted_mode_halted")
    if halt_floor_b:
        tech_blockers.append("gate_b_scoped_emergency_brake_recommends_halt_floor")

    next_cmd = "python3 scripts/write_final_control_artifacts.py"
    if primary == "CONTAMINATED_SCOPE_MERGE" and not ack:
        next_cmd = (
            f"Create {ctrl / GOVERNANCE_ACK_FILENAME} from {EXAMPLE_ACK_FILENAME} "
            "(operator_cleared_for_gate_b_live + scope); then python3 scripts/write_final_control_artifacts.py"
        )

    do_not_reason = None
    if severe_technical:
        do_not_reason = "Current global production snapshot still supports halt-level risk or infrastructure anomaly."
    elif persisted_gb_halted or halt_floor_b:
        do_not_reason = "Gate B scoped adaptive state or brake still warrants halt — fix gate_b scope first."
    elif primary == "CONTAMINATED_SCOPE_MERGE" and not ack:
        do_not_reason = "Global series mixes gates; operator governance ack required for Gate B-only live switch under persisted global halt."

    return {
        "truth_version": "gate_b_global_halt_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "global_halt_source": {
            "persisted_file": str(global_path),
            "persisted_mode": pst_g.mode,
            "persisted_last_change_ts": pst_g.last_change_ts,
            "persisted_last_change_reasons": pst_g.last_change_reasons[:12],
            "gate_b_persisted_file": str(gb_path),
            "gate_b_persisted_mode": pst_b.mode,
        },
        "informational_evaluation_only": {
            "note": (
                "Uses build_live_operating_snapshot + evaluate_emergency_brake only — does not persist mode, "
                "does not call evaluate_adaptive_operating_system (avoids overwriting diagnosis artifacts)."
            ),
            "production_pnl_only": prod,
        },
        "fresh_brake_global": {
            "triggered": brake_g.triggered,
            "recommended_floor": brake_g.recommended_floor.value,
            "severity": brake_g.severity,
            "reasons": brake_g.reasons[:16],
        },
        "fresh_brake_gate_b_scoped": {
            "triggered": brake_b.triggered,
            "recommended_floor": brake_b.recommended_floor.value,
            "severity": brake_b.severity,
            "reasons": brake_b.reasons[:16],
        },
        "global_halt_primary_classification": primary,
        "classification_contributing_factors": factors,
        "global_halt_is_stale": is_stale,
        "global_halt_is_currently_authoritative": global_authoritative_for_switch,
        "gate_b_switch_live_authority": switch_authority,
        "governance_review_currently_blocking": gov_blocking,
        "operator_governance_ack_present": ack,
        "operator_governance_ack_path": str(ctrl / GOVERNANCE_ACK_FILENAME),
        "technical_blockers_remaining": tech_blockers,
        "operator_clearable_blocker": operator_clearable,
        "exact_next_command": next_cmd,
        "exact_do_not_go_live_reason_if_false": do_not_reason,
        "honesty": (
            "Persisted global halt in operating_mode_state.json is org-wide memory. Informational brake uses "
            "current production-eligible rows. Gate B switch-live may be allowed when global halt is stale or "
            "gate-mixture-driven with ack — never when technical halt floor or Gate B scope is still in true halt."
        ),
    }


def write_gate_b_example_governance_ack_template(ctrl: Path) -> None:
    ex = ctrl / EXAMPLE_ACK_FILENAME
    template = {
        "version": 1,
        "scope": "gate_b_coinbase_live_only",
        "operator_cleared_for_gate_b_live": True,
        "acknowledged_at": "2026-01-01T00:00:00+00:00",
        "statement": (
            "Operator accepts enabling Gate B Coinbase live routing while data/control/operating_mode_state.json "
            "may still show global halted until separately cleared by governance/adaptive recovery. "
            "This file does not modify global persisted mode."
        ),
    }
    if not ex.is_file():
        ex.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")


def write_gate_b_global_halt_truth_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    write_gate_b_example_governance_ack_template(ctrl)
    payload = build_gate_b_global_halt_truth(runtime_root=root)
    p = ctrl / "gate_b_global_halt_truth.json"
    p.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "gate_b_global_halt_truth.txt").write_text(json.dumps(payload, indent=2)[:20000] + "\n", encoding="utf-8")
    return {"generated_at": payload["generated_at"], "path": str(p)}


def compute_gate_b_can_be_switched_live_now(
    *,
    runtime_root: Optional[Path] = None,
    micro_live: bool,
    ready_orders: bool,
    blocked_gb_adaptive: bool,
    blocked_global_adaptive_raw: bool,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Final composer for Gate B switch-live: decouples raw persisted global halt from Gate B readiness
    when informational evidence shows stale or gate-mixture (with optional governance ack).
    """
    gh = build_gate_b_global_halt_truth(runtime_root=runtime_root)
    base = bool(micro_live and ready_orders and not blocked_gb_adaptive)
    if not base:
        return False, gh
    if not blocked_global_adaptive_raw:
        return True, gh

    tech = gh.get("technical_blockers_remaining") or []
    if tech:
        return False, gh

    primary = gh.get("global_halt_primary_classification")
    ack = bool(gh.get("operator_governance_ack_present"))

    if primary == "STALE_PERSISTED_STATE":
        return True, gh

    if primary == "CONTAMINATED_SCOPE_MERGE" and ack:
        return True, gh

    return False, gh
