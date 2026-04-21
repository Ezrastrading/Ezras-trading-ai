"""
Avenue A (Coinbase) Full Active Stack Truth — canonical operator visibility.

Provides a unified, truthful view of:
- Avenue A manager / daemon status
- Gate A manager + all sub-bots/task bots
- Gate B manager + all sub-bots/task bots  
- Support/learning/CEO/audit/research systems
- Mode-aware activation (paper/supervised_live/autonomous_live)

This module does NOT enable or disable components — it only reports truthfully
what is active, advisory, blocked, or dead based on artifact inspection.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_VERSION = "avenue_a_active_stack_truth_v1"
_REL_OUTPUT = "data/control/avenue_a_active_stack_truth.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ad(root: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=root)


def _read_json_file(p: Path) -> Optional[Dict[str, Any]]:
    try:
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _read_json(ad: LocalStorageAdapter, rel: str) -> Dict[str, Any]:
    v = ad.read_json(rel) or {}
    return v if isinstance(v, dict) else {}


def _path(root: Path, rel: str) -> Path:
    return (root / rel).resolve()


def _file_fresh(path: Path, *, max_age_sec: float) -> bool:
    if not path.is_file():
        return False
    try:
        age = time.time() - float(path.stat().st_mtime)
        return age <= float(max_age_sec)
    except OSError:
        return False


def _artifact_presence_state(
    *,
    root: Path,
    required_files: List[str],
    max_age_sec: Optional[float] = None,
) -> Dict[str, Any]:
    present: List[str] = []
    missing: List[str] = []
    stale: List[str] = []
    for rel in required_files:
        p = _path(root, rel)
        if not p.is_file():
            missing.append(rel)
            continue
        present.append(rel)
        if max_age_sec is not None and not _file_fresh(p, max_age_sec=max_age_sec):
            stale.append(rel)
    if missing and not present:
        state = "dead"
    elif missing:
        state = "partially_wired"
    elif stale:
        state = "stale"
    else:
        state = "fresh"
    return {"state": state, "present": present, "missing": missing, "stale": stale}


# =============================================================================
# COMPONENT REGISTRY — All Avenue A components with their roles
# =============================================================================

AVENUE_A_TOP_LEVEL_COMPONENTS = {
    "avenue_a_daemon": {
        "file": "trading_ai/orchestration/avenue_a_live_daemon.py",
        "role": "Main Avenue A live daemon runner — executes Gate A cycles",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "avenue_a_live_truth": {
        "file": "trading_ai/orchestration/avenue_a_live_daemon.py:_write_daemon_truth",
        "role": "Daemon state + live truth artifact writer",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "avenue_a_supervised_truth": {
        "file": "trading_ai/orchestration/supervised_avenue_a_truth.py",
        "role": "Supervised live Gate A proof chain validation",
        "activation_modes": ["supervised_live", "autonomous_live"],  # Used in both
    },
    "avenue_a_autonomous_runtime_truth": {
        "file": "trading_ai/orchestration/avenue_a_autonomous_runtime_truth.py",
        "role": "Autonomous-specific runtime proof verification",
        "activation_modes": ["autonomous_live"],
    },
    "daemon_live_authority": {
        "file": "trading_ai/orchestration/daemon_live_authority.py",
        "role": "Daemon authority + consistency truth",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "avenue_a_daemon_policy": {
        "file": "trading_ai/orchestration/avenue_a_daemon_policy.py",
        "role": "Daemon mode policy booleans",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "autonomous_operator_path": {
        "file": "trading_ai/orchestration/autonomous_operator_path.py",
        "role": "Operator-facing autonomous readiness summary",
        "activation_modes": ["autonomous_live"],
    },
    "rebuy_policy": {
        "file": "trading_ai/universal_execution/rebuy_policy.py",
        "role": "Trade cycle rebuy eligibility guard",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
}

GATE_A_COMPONENTS = {
    "gate_a_universe": {
        "file": "trading_ai/shark/coinbase_spot/gate_a_universe.py",
        "role": "Product selection + validation logic",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_a_config": {
        "file": "trading_ai/shark/coinbase_spot/gate_a_config.py",
        "role": "Gate A configuration parameters",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_a_market_truth": {
        "file": "trading_ai/shark/coinbase_spot/gate_a_market_truth.py",
        "role": "Market data provenance + truth",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "live_execution_validation": {
        "file": "trading_ai/runtime_proof/live_execution_validation.py",
        "role": "Gate A entry/exit execution validation",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "coinbase_spot_fill_truth": {
        "file": "trading_ai/runtime_proof/coinbase_spot_fill_truth.py",
        "role": "Fill verification + databank write",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_proof_bridge": {
        "file": "trading_ai/universal_execution/gate_b_proof_bridge.py",
        "role": "Emit universal loop proof from Gate A success",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
}

GATE_B_COMPONENTS = {
    "gate_b_engine": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_engine.py",
        "role": "Gate B momentum engine — entry/exit logic",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_config": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_config.py",
        "role": "Gate B configuration parameters",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_scanner": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_scanner.py",
        "role": "Gate B market scanner + candidate ranking",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_monitor": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_monitor.py",
        "role": "Gate B position monitoring + exit triggers",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_regime": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_regime.py",
        "role": "Market regime detection (trend/chop/volatile)",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_live_status": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_live_status.py",
        "role": "Gate B live status + activation gate",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_truth": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_truth.py",
        "role": "Gate B truth model + failure codes",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_reentry": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_reentry.py",
        "role": "Gate B re-entry controller + cooldown",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_correlation": {
        "file": "trading_ai/shark/coinbase_spot/gate_b_correlation.py",
        "role": "Portfolio correlation guard",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_liquidity_gate": {
        "file": "trading_ai/shark/coinbase_spot/liquidity_gate.py",
        "role": "Liquidity + spread gate",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_breakout_filter": {
        "file": "trading_ai/shark/coinbase_spot/breakout_filter.py",
        "role": "Breakout entry signal filter",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_momentum_scoring": {
        "file": "trading_ai/shark/coinbase_spot/momentum_scoring_engine.py",
        "role": "Momentum scoring engine",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "gate_b_global_halt_truth": {
        "file": "trading_ai/reports/gate_b_global_halt_truth.py",
        "role": "Global halt classification + truth",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_control_truth": {
        "file": "trading_ai/reports/gate_b_control_truth.py",
        "role": "Gate B control bundle + operating mode",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "gate_b_final_go_live": {
        "file": "trading_ai/reports/gate_b_final_go_live_truth.py",
        "role": "Gate B final go-live truth",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
}

SUPPORT_SYSTEMS = {
    "learning_engine": {
        "file": "trading_ai/shark/lessons.py",
        "role": "Trade lesson capture + runtime influence",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "lesson_runtime_influence": {
        "file": "trading_ai/shark/lesson_runtime_influence.py",
        "role": "Apply lessons to exit params + rebuy",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "progression": {
        "file": "trading_ai/shark/progression.py",
        "role": "Trading progression tracking",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "mission": {
        "file": "trading_ai/shark/mission.py",
        "role": "Mission state + daily goal tracking",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "ceo_sessions": {
        "file": "trading_ai/shark/ceo_sessions.py",
        "role": "Claude CEO autonomous strategy sessions",
        "schedule": "4x daily ET (08:00, 12:00, 17:00, 22:00)",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "ceo_daily_orchestration": {
        "file": "trading_ai/global_layer/ceo_daily_orchestration.py",
        "role": "Daily CEO review + bot orchestration",
        "schedule": "daily",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "daily_trading_summary": {
        "file": "trading_ai/reports/daily_trading_summary.py",
        "role": "Daily trading summary report",
        "schedule": "daily",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "bot_registry": {
        "file": "trading_ai/global_layer/bot_registry.py",
        "role": "Bot registry + lifecycle management",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "bot_hierarchy": {
        "file": "trading_ai/global_layer/bot_hierarchy/registry.py",
        "role": "Bot hierarchy registry (Avenue Master, Gate Managers)",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "capital_governor": {
        "file": "trading_ai/global_layer/capital_governor.py",
        "role": "Capital allocation + live quote guard",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "governance_order_gate": {
        "file": "trading_ai/global_layer/governance_order_gate.py",
        "role": "Governance order validation + duplicate guard",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "review_scheduler": {
        "file": "trading_ai/global_layer/review_scheduler.py",
        "role": "Review scheduling + packet management",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "ai_review_packet_builder": {
        "file": "trading_ai/global_layer/ai_review_packet_builder.py",
        "role": "AI review packet assembly",
        "schedule": "on_demand",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "promotion_contract_engine": {
        "file": "trading_ai/global_layer/promotion_contract_engine.py",
        "role": "Bot promotion/demotion contract evaluation",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "orchestration_truth_chain": {
        "file": "trading_ai/global_layer/orchestration_truth_chain.py",
        "role": "Orchestration truth chain + conflict detection",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "autonomous_backbone_status": {
        "file": "trading_ai/global_layer/autonomous_backbone_status.py",
        "role": "Autonomous backbone health status",
        "schedule": "continuous",
        "activation_modes": ["autonomous_live"],
    },
    "runtime_artifact_refresh_manager": {
        "file": "trading_ai/reports/runtime_artifact_refresh_manager.py",
        "role": "Runtime artifact staleness + refresh",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "databank_writer": {
        "file": "trading_ai/nte/databank/",
        "role": "Databank trade event persistence",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "supabase_sync": {
        "file": "trading_ai/supabase/",
        "role": "Supabase remote sync",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
}

SAFETY_GUARDS = {
    "kill_switch_engine": {
        "file": "trading_ai/safety/kill_switch_engine.py",
        "role": "Kill switch evaluation + emergency brake",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "failsafe_guard": {
        "file": "trading_ai/safety/failsafe_guard.py",
        "role": "Failsafe state + duplicate trade guard",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live", "paper", "tick_only"],
    },
    "duplicate_guard": {
        "file": "trading_ai/nte/hardening/validation_duplicate_guard.py",
        "role": "Validation duplicate trade isolation",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
    "live_order_guard": {
        "file": "trading_ai/nte/hardening/live_order_guard.py",
        "role": "Live order guard + execution lock",
        "schedule": "continuous",
        "activation_modes": ["supervised_live", "autonomous_live"],
    },
}


# =============================================================================
# MODE DETECTION
# =============================================================================

def _daemon_min_interval_sec(mode: str) -> float:
    """
    Mirror Avenue A daemon scheduling intervals.

    This is used only for *status freshness thresholds* (truthful "active now" inference),
    not to change daemon behavior.
    """
    if mode == "supervised_live":
        v = (os.environ.get("EZRAS_AVENUE_A_SUPERVISED_MIN_INTERVAL_SEC") or "300").strip() or "300"
        try:
            return float(v)
        except ValueError:
            return 300.0
    if mode == "autonomous_live":
        v = (os.environ.get("EZRAS_AVENUE_A_AUTONOMOUS_MIN_INTERVAL_SEC") or "60").strip() or "60"
        try:
            return float(v)
        except ValueError:
            return 60.0
    return 60.0


def _mtime_age_sec(p: Path) -> Optional[float]:
    try:
        st = p.stat()
    except OSError:
        return None
    return max(0.0, time.time() - float(st.st_mtime))


def _detect_effective_mode(*, runtime_root: Path) -> Dict[str, Any]:
    """Detect the effective operating mode from env + artifacts."""
    from trading_ai.orchestration.avenue_a_daemon_policy import (
        avenue_a_daemon_mode,
        avenue_a_effective_autonomous_execution_tier,
        avenue_a_is_autonomous_family,
    )

    mode = avenue_a_daemon_mode()
    root = Path(runtime_root).resolve()

    # Determine effective tier
    if avenue_a_is_autonomous_family(mode):
        tier = avenue_a_effective_autonomous_execution_tier(runtime_root=root)
    else:
        tier = "not_autonomous"

    # Determine if live orders are allowed
    live_orders_allowed = False
    live_blockers: List[str] = []

    if mode == "supervised_live":
        from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_supervised_runtime_allowed
        live_orders_allowed, why = avenue_a_supervised_runtime_allowed(runtime_root=root)
        if not live_orders_allowed:
            live_blockers = [why] if why else ["supervised_runtime_not_allowed"]
    elif avenue_a_is_autonomous_family(mode):
        from trading_ai.orchestration.autonomous_daemon_live_contract import autonomous_daemon_may_submit_live_orders
        live_orders_allowed, blockers = autonomous_daemon_may_submit_live_orders(runtime_root=root)
        live_blockers = blockers if not live_orders_allowed else []

    # Normalize to operator-facing modes required by the mission contract.
    if mode == "paper_execution":
        effective_mode = "paper"
    elif mode == "tick_only":
        effective_mode = "tick_only"
    elif mode == "autonomous_live_enabled" and tier != "live_enabled":
        effective_mode = "autonomous_live_armed_off"
    else:
        effective_mode = mode

    return {
        "daemon_mode_env": mode,
        "effective_mode": effective_mode,
        "effective_autonomous_tier": tier,
        "live_orders_allowed": live_orders_allowed,
        "live_order_blockers": live_blockers,
        "is_autonomous_family": avenue_a_is_autonomous_family(mode),
    }


# =============================================================================
# COMPONENT STATE DETECTION
# =============================================================================

def _check_component_state(
    component_name: str,
    component_def: Dict[str, Any],
    *,
    effective_mode: str,
    runtime_root: Path,
) -> Dict[str, Any]:
    """Truthful component state based on mode + runtime evidence (not code presence)."""
    activation_modes = component_def.get("activation_modes", [])

    # Determine if component should be active in current mode
    should_be_active = effective_mode in activation_modes

    # Safety guards: cannot be proven active by import; claim per-contract only.
    if "guard" in component_name or component_name in SAFETY_GUARDS:
        # Guards are always "active" in the sense that they enforce
        return {
            "component_name": component_name,
            "role": component_def.get("role", ""),
            "current_state": "active" if should_be_active else "advisory_only",
            "activation_condition": f"mode in {activation_modes}",
            "truth_source": component_def.get("file", ""),
            "should_be_active": should_be_active,
            "evidence": {"type": "contract"},
        }

    # Support systems: only claim ACTIVE with fresh on-disk evidence.
    if component_name in SUPPORT_SYSTEMS:
        state = "advisory_only"
        evidence: Dict[str, Any] = {"type": "artifact_inspection"}

        req: List[str] = []
        max_age: Optional[float] = None
        if component_name == "autonomous_backbone_status":
            req = ["data/control/autonomous_backbone_status.json"]
            max_age = 3600.0
        elif component_name == "runtime_artifact_refresh_manager":
            req = ["data/control/runtime_artifact_refresh_truth.json"]
            max_age = 3600.0
        elif component_name == "orchestration_truth_chain":
            req = ["data/control/orchestration_truth_chain.json"]
            max_age = 6 * 3600.0
        elif component_name in ("learning_engine", "lesson_runtime_influence"):
            req = ["data/control/lessons_runtime_truth.json"]
            max_age = 6 * 3600.0
        elif component_name == "ceo_daily_orchestration":
            req = ["data/control/ceo_session_truth.json"]
            max_age = 24 * 3600.0

        if req:
            pres = _artifact_presence_state(root=runtime_root, required_files=req, max_age_sec=max_age)
            evidence.update({"required": req, "freshness_window_sec": max_age, "presence": pres})
            if pres["state"] == "fresh":
                state = "active" if should_be_active else "advisory_only"
            elif pres["state"] in ("stale", "partially_wired"):
                state = "blocked" if should_be_active else "advisory_only"
            else:
                state = "dead" if should_be_active else "advisory_only"
        else:
            state = "advisory_only" if should_be_active else "disabled"
        return {
            "component_name": component_name,
            "role": component_def.get("role", ""),
            "current_state": state,
            "activation_condition": f"mode in {activation_modes}",
            "truth_source": component_def.get("file", ""),
            "should_be_active": should_be_active,
            "evidence": evidence,
        }

    # Default: do not claim ACTIVE without a runtime evidence contract.
    state = "advisory_only" if should_be_active else ("advisory_only" if "paper" in activation_modes or "tick_only" in activation_modes else "disabled")

    return {
        "component_name": component_name,
        "role": component_def.get("role", ""),
        "current_state": state,
        "activation_condition": f"mode in {activation_modes}",
        "truth_source": component_def.get("file", ""),
        "should_be_active": should_be_active,
        "evidence": {"type": "no_runtime_evidence_contract"},
    }


# =============================================================================
# MANAGER STATUS DETECTION
# =============================================================================

def _get_manager_status(*, runtime_root: Path) -> Dict[str, Any]:
    """Get status of Avenue A manager, Gate A manager, Gate B manager."""
    root = Path(runtime_root).resolve()
    ad = _ad(root)

    # Avenue A daemon state
    daemon_state = _read_json(ad, "data/control/avenue_a_daemon_state.json")
    daemon_truth = _read_json(ad, "data/control/avenue_a_daemon_live_truth.json")
    # Note: runtime_runner artifacts are supporting evidence only; Avenue A daemon is the canonical manager.
    last_cycle = _read_json(ad, "data/control/runtime_runner_last_cycle.json")
    lock_path = root / "data" / "control" / "runtime_runner.lock"
    lock_present = lock_path.is_file()

    # Daemon mode
    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_daemon_mode
    mode = avenue_a_daemon_mode()

    # Avenue A manager (daemon) "active" is a *runtime inference*:
    # - fresh daemon truth is the primary evidence (daemon ran recently)
    # - lock present implies a supervisor loop currently owns the runner (supporting)
    # - otherwise, a very recent runtime_runner_last_cycle suggests recent activity (supporting)
    freshness_window = max(120.0, 2.5 * _daemon_min_interval_sec(mode))
    last_cycle_age = _mtime_age_sec(root / "data" / "control" / "runtime_runner_last_cycle.json")
    daemon_recent = (last_cycle_age is not None) and (last_cycle_age <= freshness_window)
    daemon_truth_age = _mtime_age_sec(root / "data" / "control" / "avenue_a_daemon_live_truth.json")
    daemon_truth_recent = (daemon_truth_age is not None) and (daemon_truth_age <= freshness_window) and bool(
        daemon_truth.get("truth_version")
    )
    avenue_a_manager_active = bool(daemon_truth_recent or lock_present or daemon_recent)

    # Gate A manager status - derived from execution proof freshness
    gate_a_proof_path = root / "execution_proof" / "live_execution_validation.json"
    gate_a_proof = _read_json(ad, "execution_proof/live_execution_validation.json")
    gate_a_age = _mtime_age_sec(gate_a_proof_path)
    gate_a_recent = (gate_a_age is not None) and (gate_a_age <= freshness_window)
    # Truth: Gate A live manager is only "active" when the daemon is in a live-capable mode
    # AND not in autonomous armed_off.
    if mode in ("disabled", "tick_only", "paper_execution", "autonomous_live_armed_off"):
        gate_a_manager_active = False
    else:
        if mode in ("autonomous_live", "autonomous_live_enabled"):
            from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_effective_autonomous_execution_tier

            if avenue_a_effective_autonomous_execution_tier(runtime_root=root) == "armed_off":
                gate_a_manager_active = False
            else:
                gate_a_manager_active = bool(
                    avenue_a_manager_active
                    and (daemon_state.get("last_supervised_live_order_attempted") or gate_a_recent)
                )
        else:
            gate_a_manager_active = bool(
                avenue_a_manager_active
                and (daemon_state.get("last_supervised_live_order_attempted") or gate_a_recent)
            )

    # Gate B manager status (production tick pipeline; scan-only)
    gate_b_status_path = root / "data" / "control" / "gate_b_live_status.json"
    gate_b_tick_path = root / "data" / "control" / "gate_b_last_production_tick.json"
    gate_b_status = _read_json(ad, "data/control/gate_b_live_status.json")
    gb_age = min(
        [x for x in [_mtime_age_sec(gate_b_status_path), _mtime_age_sec(gate_b_tick_path)] if x is not None] or [1e18]
    )
    gate_b_recent = gb_age <= freshness_window
    gate_b_manager_active = bool(gate_b_recent and (gate_b_status != {}))

    return {
        "avenue_a_manager": {
            "active": avenue_a_manager_active,
            "component": "avenue_a_daemon",
            "daemon_mode": mode,
            "runtime_runner_lock_present": lock_present,
            "freshness_window_sec": freshness_window,
            "runtime_runner_last_cycle_age_sec": last_cycle_age,
            "avenue_a_daemon_live_truth_age_sec": daemon_truth_age,
            "last_cycle_ok": daemon_state.get("last_supervised_cycle_ok"),
            "consecutive_ok_cycles": daemon_state.get("consecutive_autonomous_ok_cycles", 0),
            "daemon_truth_present": bool(daemon_truth),
        },
        "gate_a_manager": {
            "active": gate_a_manager_active,
            "component": "live_execution_validation",
            "last_execution_proven": gate_a_proof.get("FINAL_EXECUTION_PROVEN"),
            "last_trade_id": gate_a_proof.get("trade_id"),
            "live_execution_validation_age_sec": gate_a_age,
        },
        "gate_b_manager": {
            "active": gate_b_manager_active,
            "component": "gate_b_live_status",
            "status": gate_b_status.get("gate_b_live_status", "UNKNOWN"),
            "can_switch_live": gate_b_status.get("gate_b_can_be_switched_live_now", False),
            "gate_b_status_age_sec": _mtime_age_sec(gate_b_status_path),
            "gate_b_last_production_tick_age_sec": _mtime_age_sec(gate_b_tick_path),
        },
    }


# =============================================================================
# SUB-BOT STATUS DETECTION
# =============================================================================

def _get_subbot_status(*, runtime_root: Path) -> Dict[str, Any]:
    """Get status of all Gate A and Gate B sub-bots."""
    root = Path(runtime_root).resolve()
    _ = _ad(root)
    mode_info = _detect_effective_mode(runtime_root=root)
    mode = str(mode_info.get("effective_mode") or mode_info.get("daemon_mode_env") or "disabled")

    # Gate A sub-bots
    gate_a_subbots = []
    for name, defn in GATE_A_COMPONENTS.items():
        state = _check_component_state(name, defn, effective_mode=mode, runtime_root=root)
        gate_a_subbots.append({
            "bot_id": name,
            "state": state["current_state"],
            "role": state["role"],
            "truth_source": state["truth_source"],
            "evidence": state.get("evidence") or {},
        })

    # Gate B sub-bots
    gate_b_subbots = []
    for name, defn in GATE_B_COMPONENTS.items():
        state = _check_component_state(name, defn, effective_mode=mode, runtime_root=root)
        gate_b_subbots.append({
            "bot_id": name,
            "state": state["current_state"],
            "role": state["role"],
            "truth_source": state["truth_source"],
            "evidence": state.get("evidence") or {},
        })

    return {
        "gate_a_subbots": gate_a_subbots,
        "gate_b_subbots": gate_b_subbots,
        "gate_a_active_count": sum(1 for b in gate_a_subbots if b["state"] == "active"),
        "gate_b_active_count": sum(1 for b in gate_b_subbots if b["state"] == "active"),
    }


# =============================================================================
# SUPPORT SYSTEMS STATUS
# =============================================================================

def _get_support_systems_status(*, runtime_root: Path) -> Dict[str, Any]:
    """Get status of support/learning/CEO/audit systems."""
    root = Path(runtime_root).resolve()
    ad = _ad(root)
    mode_info = _detect_effective_mode(runtime_root=root)
    mode = str(mode_info.get("effective_mode") or mode_info.get("daemon_mode_env") or "disabled")

    support_systems = []
    for name, defn in SUPPORT_SYSTEMS.items():
        state = _check_component_state(name, defn, effective_mode=mode, runtime_root=root)
        support_systems.append({
            "system_id": name,
            "state": state["current_state"],
            "role": state["role"],
            "schedule": defn.get("schedule", "continuous"),
            "truth_source": state["truth_source"],
            "evidence": state.get("evidence") or {},
        })

    # Check actual artifact freshness for key systems
    lessons_truth = ad.read_json("data/control/lessons_runtime_truth.json") or {}
    ceo_review = _read_json(ad, "data/control/ceo_session_truth.json")
    orchestration = ad.read_json("data/control/orchestration_truth_chain.json") or {}

    # Update states based on actual artifacts
    for sys in support_systems:
        if sys["system_id"] == "lesson_runtime_influence":
            if lessons_truth.get("truth_version"):
                sys["last_artifact"] = lessons_truth.get("generated_at")
        elif sys["system_id"] == "ceo_daily_orchestration":
            if ceo_review.get("truth_version"):
                sys["last_artifact"] = ceo_review.get("generated_at")
        elif sys["system_id"] == "orchestration_truth_chain":
            if orchestration.get("truth_version"):
                sys["last_artifact"] = orchestration.get("generated_at")

    return {
        "support_systems": support_systems,
        "active_count": sum(1 for s in support_systems if s["state"] == "active"),
        "advisory_count": sum(1 for s in support_systems if s["state"] == "advisory_only"),
    }


# =============================================================================
# MAIN ACTIVE STACK BUILDER
# =============================================================================

def build_avenue_a_active_stack_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Build the canonical Avenue A active stack truth artifact.

    Returns comprehensive status of:
    - Avenue A manager
    - Gate A manager + sub-bots
    - Gate B manager + sub-bots
    - Support systems (learning, CEO, reviews, etc.)
    - Safety guards
    - Mode-specific blockers
    """
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = _ad(root)

    # Get mode info
    mode_info = _detect_effective_mode(runtime_root=root)
    effective_mode = str(mode_info.get("effective_mode") or mode_info.get("daemon_mode_env") or "disabled")

    # Get manager status (runtime-inferred, artifact-backed)
    manager_status = _get_manager_status(runtime_root=root)

    # Get sub-bot status
    subbot_status = _get_subbot_status(runtime_root=root)

    # Get support systems status
    support_status = _get_support_systems_status(runtime_root=root)

    # Get safety guards status
    safety_guards = []
    for name, defn in SAFETY_GUARDS.items():
        state = _check_component_state(name, defn, effective_mode=effective_mode, runtime_root=root)
        safety_guards.append({
            "guard_id": name,
            "state": state["current_state"],
            "role": state["role"],
        })

    # Get blockers from existing authority
    from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path
    autonomous_path = build_autonomous_operator_path(runtime_root=root)

    from trading_ai.orchestration.avenue_a_daemon_policy import avenue_a_supervised_runtime_allowed
    sup_ok, sup_why = avenue_a_supervised_runtime_allowed(runtime_root=root)

    # Duplicate guard truth (failsafe_status.json)
    fs_path = root / "data" / "control" / "failsafe_status.json"
    fs = _read_json_file(fs_path) or {}
    dup_last = fs.get("duplicate_guard_last_block") if isinstance(fs.get("duplicate_guard_last_block"), dict) else {}
    dup_status = {
        "active": (True if (fs.get("duplicate_window_effective_sec") is not None) else False) if fs else None,
        "duplicate_window_sec": fs.get("duplicate_window_sec"),
        "duplicate_window_effective_sec": fs.get("duplicate_window_effective_sec"),
        "cooldown_remaining_sec": dup_last.get("cooldown_remaining_sec"),
        "trigger_trade_id": dup_last.get("trigger_trade_id"),
        "key": dup_last.get("key"),
        "last_block_ts": dup_last.get("ts"),
        "truth_source": str(fs_path),
        "honesty": "Derived from data/control/failsafe_status.json. Guard is enforced at live_order_guard->failsafe_guard.",
    }

    # Fee awareness (proof + realized pnl)
    latest_gate_a = _read_json_file(root / "execution_proof" / "live_execution_validation.json") or {}
    # Gate A validation proof writes fee-aware realized PnL under these canonical keys.
    fee_model_present = bool(
        latest_gate_a.get("fees_paid") is not None
        or latest_gate_a.get("gross_pnl") is not None
        or latest_gate_a.get("net_pnl") is not None
    )
    fee_awareness_status = {
        "fee_model_present": fee_model_present,
        "fee_applied_to_trade_pnl": bool(latest_gate_a.get("net_pnl") is not None and latest_gate_a.get("fees_paid") is not None),
        "fee_applied_to_progression": None,
        "fee_applied_to_summary_views": None,
        "truth_source": "execution_proof/live_execution_validation.json:{net_pnl,fees_paid,gross_pnl}",
        "honesty": "This status inspects the latest Gate A proof for fee-aware net_pnl. Higher-level rollups must separately verify they use net_pnl_usd fields.",
    }

    # Assemble the full active stack
    active_stack = {
        "truth_version": _TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "effective_mode": mode_info.get("effective_mode") or effective_mode,
        "effective_tier": mode_info["effective_autonomous_tier"],
        "live_order_submission_allowed": mode_info["live_orders_allowed"],

        # Manager status
        "avenue_a_manager_active": manager_status["avenue_a_manager"]["active"],
        "gate_a_manager_active": manager_status["gate_a_manager"]["active"],
        "gate_b_manager_active": manager_status["gate_b_manager"]["active"],
        "manager_details": manager_status,

        # Sub-bot status
        "gate_a_subbots_active": [b["bot_id"] for b in subbot_status["gate_a_subbots"] if b["state"] == "active"],
        "gate_a_subbots_advisory": [b["bot_id"] for b in subbot_status["gate_a_subbots"] if b["state"] == "advisory_only"],
        "gate_b_subbots_active": [b["bot_id"] for b in subbot_status["gate_b_subbots"] if b["state"] == "active"],
        "gate_b_subbots_advisory": [b["bot_id"] for b in subbot_status["gate_b_subbots"] if b["state"] == "advisory_only"],
        "subbot_details": subbot_status,

        # Support systems
        "support_systems_active": [s["system_id"] for s in support_status["support_systems"] if s["state"] == "active"],
        "support_systems_advisory": [s["system_id"] for s in support_status["support_systems"] if s["state"] == "advisory_only"],
        "support_systems_details": support_status,

        # Safety guards
        "safety_guards": safety_guards,

        # Blockers
        "supervised_blockers": [] if sup_ok else [sup_why],
        "autonomous_blockers": autonomous_path.get("active_blockers", []),
        "live_order_blockers": mode_info["live_order_blockers"],
        "historical_notes": autonomous_path.get("historical_notes") or [],
        "active_autonomous_blockers": autonomous_path.get("active_blockers", []),
        "dual_gate_live_orders_ok": bool(mode_info["live_orders_allowed"]) if mode_info.get("is_autonomous_family") else None,

        "duplicate_guard_status": dup_status,
        "fee_awareness_status": fee_awareness_status,

        # Advisory-only components (explicitly non-execution)
        "advisory_only_components": (
            [b["bot_id"] for b in subbot_status["gate_a_subbots"] if b["state"] == "advisory_only"] +
            [b["bot_id"] for b in subbot_status["gate_b_subbots"] if b["state"] == "advisory_only"] +
            [s["system_id"] for s in support_status["support_systems"] if s["state"] == "advisory_only"]
        ),

        # Dead or unwired components
        "dead_or_unwired_components": [
            b["bot_id"] for b in subbot_status["gate_a_subbots"] + subbot_status["gate_b_subbots"]
            if b["state"] == "dead"
        ],

        # Honesty statement
        "honesty": (
            "Evidence-first: managers/sub-bots/support systems are only marked ACTIVE when there is fresh on-disk runtime evidence "
            "(daemon truth, Gate B tick output, execution proofs, or explicit supporting truth artifacts). "
            "Code presence alone remains advisory-only. Live order submission is separately gated by supervised policy or autonomous dual-gate."
        ),
    }

    return active_stack


def write_avenue_a_active_stack_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Write the active stack truth artifact to disk."""
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = _ad(root)

    payload = build_avenue_a_active_stack_truth(runtime_root=root)
    ad.write_json(_REL_OUTPUT, payload)
    ad.write_text(_REL_OUTPUT.replace(".json", ".txt"), json.dumps(payload, indent=2) + "\n")

    return payload


def read_avenue_a_active_stack_truth(*, runtime_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the active stack truth artifact from disk."""
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = _ad(root)
    return ad.read_json(_REL_OUTPUT)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def cli_active_stack_status() -> None:
    """CLI entry point: print active stack JSON to stdout."""
    import sys
    payload = build_avenue_a_active_stack_truth()
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    cli_active_stack_status()
