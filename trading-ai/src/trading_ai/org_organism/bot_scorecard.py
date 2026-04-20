"""Bot scorecards — advisory; derived from registry + hierarchy signals."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.org_organism.io_utils import append_jsonl, write_json_atomic
from trading_ai.org_organism.paths import (
    avenue_master_scorecard_path,
    bot_scorecard_path,
    gate_manager_scorecard_path,
    organism_advisory_queue_path,
    worker_bot_scorecard_path,
)
from trading_ai.runtime_paths import ezras_runtime_root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_bot_row(bot: Dict[str, Any]) -> Dict[str, Any]:
    bid = str(bot.get("bot_id") or "unknown")
    role = str(bot.get("role") or "")
    perm = str(bot.get("permission_level") or "")
    stale = bool(bot.get("stale"))
    hb = bot.get("last_heartbeat_at")
    usefulness = 0.55
    if perm == "advisory_only":
        usefulness += 0.05
    if stale:
        usefulness -= 0.25
    correctness = 0.6 if not stale else 0.35
    honesty = 0.7 if perm == "advisory_only" else 0.5
    evidence_quality = 0.55
    timeliness = 0.65 if hb else 0.4
    false_positive_rate = 0.15 if stale else 0.08
    wasted_action_rate = 0.12 if stale else 0.06
    redundant_work_rate = 0.1
    coordination = 0.5
    improvement = 0.45
    promotion_contrib = 0.4 if "promotion" in role.lower() else 0.35
    discipline = 0.7 if perm == "advisory_only" else 0.55
    return {
        "bot_id": bid,
        "role": role,
        "permission_level": perm,
        "usefulness": round(max(0.0, min(1.0, usefulness)), 3),
        "correctness": round(correctness, 3),
        "honesty": round(honesty, 3),
        "evidence_quality": round(evidence_quality, 3),
        "timeliness": round(timeliness, 3),
        "false_positive_rate": round(false_positive_rate, 3),
        "wasted_action_rate": round(wasted_action_rate, 3),
        "redundant_work_rate": round(redundant_work_rate, 3),
        "coordination_quality": round(coordination, 3),
        "improvement_contribution": round(improvement, 3),
        "promotion_contribution": round(promotion_contrib, 3),
        "operational_discipline": round(discipline, 3),
        "advisory_only": True,
        "scores_are_heuristic": True,
    }


def build_bot_scorecard_bundle(*, runtime_root: Optional[Path] = None, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    rows = [_score_bot_row(b) for b in bots if isinstance(b, dict)]

    masters = [r for r in rows if "master" in str(r.get("role") or "").lower() or "avenue" in str(r.get("role") or "").lower()]
    managers = [r for r in rows if "manager" in str(r.get("role") or "").lower()]
    workers = [r for r in rows if r not in masters and r not in managers]

    bundle = {
        "truth_version": "bot_scorecard_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "no_automatic_permission_changes": True,
        "bots": rows,
    }
    write_json_atomic(bot_scorecard_path(root), bundle)
    write_json_atomic(
        avenue_master_scorecard_path(root),
        {"truth_version": "avenue_master_scorecard_v1", "generated_at": _now_iso(), "advisory_only": True, "rows": masters},
    )
    write_json_atomic(
        gate_manager_scorecard_path(root),
        {"truth_version": "gate_manager_scorecard_v1", "generated_at": _now_iso(), "advisory_only": True, "rows": managers},
    )
    write_json_atomic(
        worker_bot_scorecard_path(root),
        {"truth_version": "worker_bot_scorecard_v1", "generated_at": _now_iso(), "advisory_only": True, "rows": workers},
    )

    advisory = {
        "ts": _now_iso(),
        "kind": "bot_scorecard_advisory",
        "advisory_only": True,
        "enforceable": False,
        "low_usefulness_bot_ids": [r["bot_id"] for r in rows if float(r.get("usefulness") or 0) < 0.45][:12],
        "stale_bot_ids": [str(b.get("bot_id")) for b in bots if isinstance(b, dict) and b.get("stale")][:12],
    }
    append_jsonl(organism_advisory_queue_path(root), advisory)

    return {
        "bot_scorecard": bundle,
        "avenue_master_scorecard": masters,
        "gate_manager_scorecard": managers,
        "worker_bot_scorecard": workers,
        "advisory_hook": advisory,
    }
