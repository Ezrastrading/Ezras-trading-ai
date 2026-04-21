"""Convert simulation outputs into scoped shadow tasks (avenue, gate, bot)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.bot_types import BotRole
from trading_ai.global_layer.task_router import route_task_shadow
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _iter_scopes(registry_path: Optional[Path] = None) -> List[Tuple[str, str, str]]:
    """(avenue, gate, representative_bot_id)"""
    reg = load_registry(registry_path)
    out: List[Tuple[str, str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for b in reg.get("bots") or []:
        if not isinstance(b, dict):
            continue
        av = str(b.get("avenue") or "A").strip() or "A"
        gate = str(b.get("gate") or "none").strip() or "none"
        bid = str(b.get("bot_id") or "unassigned")
        key = (av, gate)
        if key in seen:
            continue
        seen.add(key)
        out.append((av, gate, bid))
    return out or [("A", "none", "sim_scope_bot")]


def _mission_priority_boost(*, pace_state: str, active_goal_id: str, task_kind: str) -> int:
    """Match mission consumer semantics: pace changes priorities, not sizing."""
    ps = str(pace_state or "unknown")
    gid = str(active_goal_id or "GOAL_A")
    boost = 0
    if ps == "behind_pace":
        boost += {"risk": 130, "compare": 90, "learn": 100}.get(task_kind, 0)
    elif ps == "ahead_of_pace":
        boost += {"risk": 80, "compare": 110, "learn": 120}.get(task_kind, 0)
    else:
        boost += {"risk": 100, "compare": 100, "learn": 100}.get(task_kind, 0)
    if gid == "GOAL_A":
        boost += {"risk": 30, "compare": 40, "learn": 50}.get(task_kind, 0)
    else:
        boost += {"risk": 50, "compare": 35, "learn": 40}.get(task_kind, 0)
    return int(boost)


def emit_simulation_tasks(
    *,
    runtime_root: Optional[Path] = None,
    pnl_doc: Dict[str, Any],
    comparisons_doc: Dict[str, Any],
    regression_doc: Optional[Dict[str, Any]] = None,
    anomaly_note: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Emit shadow tasks:
    - risk_reduction when aggregate sim PnL negative
    - comparisons::avenue biased to weakest avenue / strategy
    - learning/research tasks on losses / anomalies
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    scopes = _iter_scopes()
    net = float(pnl_doc.get("net_total_usd") or 0.0)
    emitted: List[Dict[str, Any]] = []
    evidence_pnl = str(root / "data" / "control" / "sim_pnl.json")
    evidence_cmp = str(root / "data" / "control" / "sim_comparisons.json")

    reg = regression_doc or {}
    if reg.get("emit_corrective_tasks"):
        ev = str(root / "data" / "control" / "sim_pnl.json")
        for av, gate, bot_scope in scopes:
            t = route_task_shadow(
                avenue=str(av),
                gate=str(gate),
                task_type="regression_drift::sim_corrective",
                source_bot_id="sim_engine",
                role=BotRole.RISK.value,
                evidence_ref=ev,
            )
            t["scope"] = {"avenue": av, "gate": gate, "bot": bot_scope}
            t["simulation"] = {"regression": reg}
            emitted.append(t)

    if net < 0:
        for av, gate, bot_scope in scopes:
            t = route_task_shadow(
                avenue=str(av),
                gate=str(gate),
                task_type="risk_reduction",
                source_bot_id="sim_engine",
                role=BotRole.RISK.value,
                evidence_ref=evidence_pnl,
            )
            t["scope"] = {"avenue": av, "gate": gate, "bot": bot_scope}
            t["simulation"] = {"reason": "negative_sim_pnl", "net_total_usd": net}
            emitted.append(t)

    weakest = str(comparisons_doc.get("weakest_strategy") or comparisons_doc.get("weakest_avenue") or "")
    for av, gate, bot_scope in scopes:
        t = route_task_shadow(
            avenue=str(av),
            gate=str(gate),
            task_type="comparisons::avenue",
            source_bot_id="sim_engine",
            role=BotRole.LEARNING.value,
            evidence_ref=evidence_cmp,
        )
        boost = 140 if weakest and (weakest in str(av) or weakest in str(gate)) else 40
        t["priority"] = int(t.get("priority") or 0) + boost
        t["scope"] = {"avenue": av, "gate": gate, "bot": bot_scope}
        t["simulation"] = {"weakest_hint": weakest or None}
        emitted.append(t)

    if net < 0 or anomaly_note:
        for av, gate, bot_scope in scopes:
            t = route_task_shadow(
                avenue=str(av),
                gate=str(gate),
                task_type="learning::sim_anomaly_review",
                source_bot_id="sim_engine",
                role=BotRole.LEARNING.value,
                evidence_ref=evidence_pnl,
            )
            t["scope"] = {"avenue": av, "gate": gate, "bot": bot_scope}
            t["simulation"] = {"note": anomaly_note or "loss_or_negative_pnl"}
            emitted.append(t)

    plan_path = root / "data" / "control" / "mission_goals_operating_plan.json"
    plan = _read_json(plan_path)
    pace = (plan.get("pace") or {}) if isinstance(plan.get("pace"), dict) else {}
    ps = str(pace.get("pace_state") or "unknown")
    ag = (plan.get("active_goal") or {}) if isinstance(plan.get("active_goal"), dict) else {}
    gid = str(ag.get("id") or "GOAL_A")

    for t in emitted:
        tt = str(t.get("task_type") or "")
        kind = "risk" if "risk" in tt else ("compare" if "comparisons" in tt else "learn")
        t["priority"] = int(t.get("priority") or 0) + _mission_priority_boost(
            pace_state=ps, active_goal_id=gid, task_kind=kind
        )
        t["mission_influence"] = {"pace_state": ps, "active_goal_id": gid}

    return emitted


def write_sim_tasks_snapshot(*, runtime_root: Path, rows: List[Dict[str, Any]]) -> Path:
    p = runtime_root / "data" / "control" / "sim_tasks.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "truth_version": "sim_tasks_snapshot_v1",
        "generated_at": _iso(),
        "count": len(rows),
        "tasks": rows[-200:],
        "honesty": "Shadow tasks from simulation; does not enable venue execution.",
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p
