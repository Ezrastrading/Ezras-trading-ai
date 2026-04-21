"""Operator-facing Gate B live readiness (honest, env + artifact driven)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import resolve_coinbase_runtime_product_policy
from trading_ai.runtime_paths import ezras_runtime_root


def _root() -> Path:
    return Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()


def load_gate_b_validation_record(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or _root())
    p = root / "data" / "control" / "gate_b_validation.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_gate_b_live_execution_enabled() -> bool:
    return os.environ.get("GATE_B_LIVE_EXECUTION_ENABLED", "").strip().lower() in ("1", "true", "yes")


def gate_b_live_status_report(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or _root())
    enabled = is_gate_b_live_execution_enabled()
    vr = load_gate_b_validation_record(runtime_root=root)
    micro_pass = bool(vr and str(vr.get("micro_validation_pass") or "").lower() in ("1", "true", "yes"))
    live_venue_micro = bool(vr and str(vr.get("live_venue_micro_validation_pass") or "").lower() in ("1", "true", "yes"))
    failed = bool(vr.get("failed_validation")) if vr else False

    if not enabled:
        state = "STATE_A_intentionally_disabled"
        validation_status = "disabled"
        ready = False
        # Staged micro-validation can still be proven while live execution stays operator-gated off.
        if micro_pass and not failed:
            readiness = "micro_validated"
        else:
            readiness = "non_live"
    elif not vr:
        state = "STATE_B_live_enabled_not_validated"
        validation_status = "pending_validation"
        ready = False
        readiness = "pending"
    elif failed:
        state = "STATE_B_live_enabled_not_validated"
        validation_status = "failed"
        ready = False
        readiness = "blocked"
    elif micro_pass and not live_venue_micro:
        state = "STATE_C_live_validated"
        validation_status = "validated"
        ready = False
        readiness = "staged_only"
    elif micro_pass and live_venue_micro:
        state = "STATE_C_live_validated"
        validation_status = "validated"
        ready = True
        readiness = "live_ready"
    else:
        state = "STATE_B_live_enabled_not_validated"
        validation_status = "pending_validation"
        ready = False
        readiness = "pending"

    lifecycle = {
        "readiness_first_20_is_gate_a_scope": True,
        "gate_b_requires_staged_micro": True,
        "live_execution_requires_operator_enable": True,
    }
    try:
        pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=False)
        coin_policy = pol.to_dict()
    except Exception:
        coin_policy = {}
    policy = {
        "universal_live_guard": True,
        "nte_execution_mode_default": os.environ.get("NTE_EXECUTION_MODE", "paper"),
        **coin_policy,
    }
    ratio_reserve_advisory = {
        "honest_classification": "advisory_runtime_context_not_order_enforced",
        "ratio_aware": True,
        "note": "Reserve/ratio context informs runtime; it is not an order router enforcement layer.",
    }
    op_disabled = not enabled
    pol_invalid = not bool(coin_policy.get("runtime_allowlist_valid", True))
    return {
        "gate_b_live_execution_enabled": bool(enabled),
        "gate_b_production_state": state,
        "gate_b_validation_status": validation_status,
        "gate_b_ready_for_live": ready,
        "gate_b_staged_micro_proven": micro_pass,
        "gate_b_live_micro_proven": live_venue_micro,
        "readiness_state": readiness,
        "coinbase_single_leg_runtime_policy": policy,
        "validation_active_products": list(coin_policy.get("validation_active_products") or []),
        "execution_active_products": list(coin_policy.get("execution_active_products") or []),
        "gate_b_disabled_by_operator_state": bool(op_disabled),
        "gate_b_disabled_by_runtime_policy": bool(pol_invalid),
        "ratio_reserve_advisory": ratio_reserve_advisory,
        "gate_b_lifecycle": lifecycle,
    }
