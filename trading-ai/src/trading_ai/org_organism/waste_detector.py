"""Detects drag / waste from artifacts — recommends cut/reduce/investigate/etc."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report
from trading_ai.org_organism.experiment_os import load_experiment_registry
from trading_ai.org_organism.io_utils import append_jsonl, read_json_dict, write_json_atomic
from trading_ai.org_organism.paths import (
    drag_sources_path,
    idle_capital_causes_path,
    promotion_bottlenecks_path,
    repeated_failure_signatures_path,
    waste_detector_snapshot_path,
    organism_advisory_queue_path,
)
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recommendation(kind: str) -> str:
    m = {
        "duplicate_blocker": "reduce",
        "stale_experiment": "cut",
        "infra_blocker": "investigate",
        "promotion_blocked": "promote",
        "code_only_insufficient": "investigate",
        "weak_strategy_attention": "pause",
    }
    return m.get(kind, "investigate")


def build_waste_detector_bundle(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    controlled = build_controlled_live_readiness_report(runtime_root=root, write_artifact=False)
    aut = build_autonomous_operator_path(runtime_root=root)

    blockers = list(aut.get("active_blockers") or [])
    shared = list(controlled.get("shared_infra_blockers_deduped") or [])
    counts = Counter(blockers)
    dupes = [b for b, n in counts.items() if n > 1]

    last_fail = ad.read_json("data/control/runtime_runner_last_failure.json")
    sig = str((last_fail or {}).get("signature") or (last_fail or {}).get("error_class") or "unknown")

    reg = load_experiment_registry(root)
    stale_exps = [
        e
        for e in (reg.get("experiments") or {}).values()
        if isinstance(e, dict) and str(e.get("status") or "") in ("draft", "queued") and not str(e.get("evidence_summary") or "").strip()
    ]

    drag_sources: List[Dict[str, Any]] = []
    for b in dupes[:10]:
        drag_sources.append(
            {"kind": "duplicate_blocker", "detail": b, "recommendation": _recommendation("duplicate_blocker")}
        )
    if len(stale_exps) > 3:
        drag_sources.append(
            {
                "kind": "stale_experiment",
                "detail": f"count_{len(stale_exps)}",
                "recommendation": _recommendation("stale_experiment"),
            }
        )
    for s in shared[:8]:
        drag_sources.append({"kind": "infra_blocker", "detail": str(s), "recommendation": "investigate"})

    promo_bn: List[Dict[str, Any]] = []
    gc = read_json_dict(root / "data" / "control" / "bot_auto_promotion_truth.json")
    if isinstance(gc, dict) and gc.get("blocked"):
        promo_bn.append({"source": "bot_auto_promotion_truth", "detail": gc.get("reason") or "blocked", "recommendation": "investigate"})

    idle_cap: List[Dict[str, Any]] = []
    if shared:
        idle_cap.append(
            {
                "cause": "shared_infra_blockers_may_prevent_sync_and_visibility",
                "refs": shared[:6],
            }
        )

    rep = {
        "truth_version": "repeated_failure_signatures_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "last_failure_signature": sig,
        "duplicate_blockers": dupes[:20],
    }
    write_json_atomic(repeated_failure_signatures_path(root), rep)
    write_json_atomic(drag_sources_path(root), {"truth_version": "drag_sources_v1", "generated_at": _now_iso(), "advisory_only": True, "sources": drag_sources})
    write_json_atomic(promotion_bottlenecks_path(root), {"truth_version": "promotion_bottlenecks_v1", "generated_at": _now_iso(), "advisory_only": True, "items": promo_bn})
    write_json_atomic(idle_capital_causes_path(root), {"truth_version": "idle_capital_causes_v1", "generated_at": _now_iso(), "advisory_only": True, "causes": idle_cap})

    snap = {
        "truth_version": "waste_detector_snapshot_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "top_recommendations": [
            {"action": "reduce", "target": "duplicate_blocker_noise", "why": "same_blocker_listed_multiple_paths"},
            {"action": "investigate", "target": "infrastructure_blockers", "why": "blocks_measurement_and_sync"},
            {"action": "pause", "target": "weak_strategy_attention", "why": "until_edge_evidence_improves"},
        ],
    }
    write_json_atomic(waste_detector_snapshot_path(root), snap)

    adv = {
        "ts": _now_iso(),
        "kind": "waste_detector_advisory",
        "advisory_only": True,
        "enforceable": False,
        "drag_source_kinds": [d["kind"] for d in drag_sources[:12]],
    }
    append_jsonl(organism_advisory_queue_path(root), adv)

    return {
        "waste_detector_snapshot": snap,
        "repeated_failure_signatures": rep,
        "drag_sources": drag_sources,
        "promotion_bottlenecks": promo_bn,
        "idle_capital_causes": idle_cap,
        "advisory_hook": adv,
    }
