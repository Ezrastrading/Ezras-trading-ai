"""
Canonical orchestration truth chain — one JSON artifact for operator visibility + audit.

Writes ``orchestration_truth_chain.json`` and optionally mirrors a short ``operator_snapshot.json``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.budget_governor import load_budget_state
from trading_ai.global_layer.execution_authority import assert_single_authority_invariant, load_authority_registry
from trading_ai.global_layer.orchestration_authority_drift import detect_authority_drift
from trading_ai.global_layer.orchestration_detection import detect_bot_registry_anomalies, detect_execution_anomalies
from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch
from trading_ai.global_layer.orchestration_paths import operator_snapshot_path, orchestration_truth_chain_path
from trading_ai.global_layer.orchestration_schema import PermissionLevel
from trading_ai.runtime_paths import ezras_runtime_root

from trading_ai.global_layer.orchestration_risk_caps import load_orchestration_risk_caps
from trading_ai.global_layer.system_mission import MISSION_VERSION, system_mission_dict


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_root_safe() -> Dict[str, Any]:
    try:
        rt = ezras_runtime_root()
        return {"path": str(rt), "resolved": True}
    except Exception as exc:
        return {"path": None, "resolved": False, "error": str(exc)}


def _shadow_vs_live_split(bots: List[Dict[str, Any]]) -> Dict[str, Any]:
    shadowish = []
    live_adj = []
    for b in bots:
        pl = str(b.get("permission_level") or "")
        life = str(b.get("lifecycle_state") or "")
        row = {"bot_id": b.get("bot_id"), "permission_level": pl, "lifecycle_state": life}
        if pl == PermissionLevel.EXECUTION_AUTHORITY.value:
            live_adj.append(row)
        else:
            shadowish.append(row)
    return {"shadow_or_non_execution": shadowish, "execution_authority_permission": live_adj}


def build_orchestration_truth_chain(*, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    ks = load_kill_switch()
    auth = load_authority_registry()
    inv_ok, inv_errs = assert_single_authority_invariant()
    drift = detect_authority_drift(registry_path=registry_path)
    anomalies = detect_execution_anomalies(registry_path=registry_path)
    bot_anom = detect_bot_registry_anomalies(registry_path=registry_path)
    bud = load_budget_state()
    try:
        risk_caps = load_orchestration_risk_caps()
    except OSError:
        risk_caps = {"error": "risk_caps_unreadable"}

    blockers: List[str] = []
    if ks.get("orchestration_frozen"):
        blockers.append("global_orchestration_frozen")
    if not inv_ok:
        blockers.append("execution_authority_invariant_failed:" + ",".join(inv_errs))
    if drift.get("blocked"):
        blockers.append("authority_drift")
    glob_used = int(bud.get("global_token_used") or 0)
    glob_cap = int(bud.get("global_daily_token_budget") or 0)
    if glob_cap and glob_used >= glob_cap:
        blockers.append("global_token_budget_exhausted")

    dl = risk_caps.get("current_daily_realized_loss_usd")
    mx = risk_caps.get("max_daily_loss_usd_global")
    try:
        if dl is not None and mx is not None and float(mx) > 0 and float(dl) >= float(mx):
            blockers.append("daily_loss_cap_breached")
    except (TypeError, ValueError):
        pass

    severe = [
        x
        for x in bot_anom
        if str(x.get("kind") or "").endswith("_breach") or x.get("kind") == "duplicate_guard_collision"
    ]
    supervised_ready = len(blockers) == 0 and len(severe) == 0
    autonomous_ready_orchestration_only = (
        supervised_ready
        and not drift.get("blocked")
        and not ks.get("orchestration_frozen")
    )

    next_cmds = [
        "python -m trading_ai.deployment orchestration-status",
        "python -m trading_ai.deployment refresh-orchestration-truth-chain",
        "python -m trading_ai.deployment orchestration-daily-ceo",
    ]
    if blockers:
        next_cmds.insert(0, "Resolve blockers[] before enabling EZRAS_ORCHESTRATION_LIVE_GATE=1")

    payload: Dict[str, Any] = {
        "truth_version": "orchestration_truth_chain_v1",
        "generated_at": _iso(),
        "system_mission": {
            "version": MISSION_VERSION,
            "active": True,
            "philosophy": system_mission_dict(),
        },
        "automation_surface": {
            "system_profitability_mission_active": True,
            "system_convergence_mission_active": True,
            "automatic_research_enabled": not bool(bud.get("review_budget_exhausted")),
            "automatic_spawn_enabled": not ks.get("orchestration_frozen"),
            "automatic_review_enabled": True,
            "automatic_ceo_enabled": True,
            "automatic_progression_enabled": True,
        },
        "runtime_root": _runtime_root_safe(),
        "registry": {"truth_version": reg.get("truth_version"), "bot_count": len(bots)},
        "orchestration_kill_switch": ks,
        "execution_authority": auth,
        "execution_authority_invariant": {"ok": inv_ok, "errors": inv_errs},
        "authority_drift": drift,
        "shadow_vs_live": _shadow_vs_live_split(bots),
        "anomalies": {"execution": anomalies, "registry": bot_anom},
        "budget_snapshot": {
            "global_daily_token_budget": bud.get("global_daily_token_budget"),
            "global_token_used": bud.get("global_token_used"),
            "per_bot_daily_token_budget": bud.get("per_bot_daily_token_budget"),
            "force_deterministic": bud.get("force_deterministic"),
        },
        "risk_caps_snapshot": {k: risk_caps.get(k) for k in ("max_daily_loss_usd_global", "current_daily_realized_loss_usd", "max_data_age_sec_for_trading") if risk_caps},
        "blockers": blockers,
        "readiness": {
            "supervised_live_operation": supervised_ready,
            "autonomous_operation": autonomous_ready_orchestration_only,
            "autonomous_operation_scoped_note": (
                "orchestration_registry_governance_only — not Avenue A autonomous_live daemon contracts "
                "(see daemon_live_switch_authority.json + avenue_a_autonomous_runtime_truth)."
            ),
            "honesty": "supervised_ready requires zero blockers and no registry anomalies; autonomous_operation here means orchestration-surface only.",
        },
        "historical_note": "Stale failures in older JSON files under runtime_root do not override this chain — refresh truth after fixes.",
        "next_operator_commands": next_cmds,
        "environment_hints": {
            "EZRAS_ORCHESTRATION_LIVE_GATE": os.environ.get("EZRAS_ORCHESTRATION_LIVE_GATE"),
            "EZRAS_BOT_REGISTRY_PATH": os.environ.get("EZRAS_BOT_REGISTRY_PATH"),
            "EZRAS_RUNTIME_ROOT": os.environ.get("EZRAS_RUNTIME_ROOT"),
        },
    }
    return payload


def write_orchestration_truth_chain(
    *,
    registry_path: Optional[Path] = None,
    write_operator_snapshot: bool = True,
) -> Dict[str, Any]:
    payload = build_orchestration_truth_chain(registry_path=registry_path)
    p = orchestration_truth_chain_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if write_operator_snapshot:
        op = operator_snapshot_path()
        op.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    from trading_ai.global_layer.orchestration_detection import write_detection_snapshot

    write_detection_snapshot(registry_path=registry_path)
    return payload
