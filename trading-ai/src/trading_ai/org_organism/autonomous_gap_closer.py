"""Autonomous gap closure — honest merge of operator path + delta vs last snapshot."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.org_organism.io_utils import read_json_dict, stable_hash, write_json_atomic
from trading_ai.org_organism.paths import (
    autonomous_gap_closer_path,
    autonomous_gap_closer_previous_path,
    autonomous_next_steps_path,
    autonomous_progress_delta_path,
)
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_blocker(b: str) -> str:
    low = b.lower()
    if "historical" in low or "stale" in low:
        return "historical_or_stale"
    if "consistent" in low or "proof" in low or "verification" in low:
        return "structural_runtime"
    return "operational_or_policy"


def build_autonomous_gap_bundle(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    cur_file = autonomous_gap_closer_path(root)
    prev_file = autonomous_gap_closer_previous_path(root)
    if cur_file.is_file():
        try:
            shutil.copyfile(cur_file, prev_file)
        except OSError:
            pass

    path_report = build_autonomous_operator_path(runtime_root=root)

    blockers: List[str] = list(path_report.get("active_blockers") or [])
    classified = [{"blocker": b, "class": _classify_blocker(str(b))} for b in blockers[:40]]

    cannot_code_only: List[str] = [
        "venue_round_trip_proof_with_real_fill",
        "operator_attended_supervised_sequence_when_policy_requires",
        "supabase_dashboard_sql_when_schema_drift",
    ]

    prev = read_json_dict(prev_file) if prev_file.is_file() else None
    cur_payload = {
        "blocker_count": len(blockers),
        "hash": stable_hash(blockers[:50]),
        "can_arm": bool(path_report.get("can_arm_autonomous_now")),
    }
    closer_than_yesterday = None
    delta: Dict[str, Any] = {}
    if isinstance(prev, dict):
        prev_h = prev.get("snapshot") if isinstance(prev.get("snapshot"), dict) else None
        if prev_h is None:
            rb = prev.get("real_runtime_blockers")
            if isinstance(rb, list):
                prev_h = {"blocker_count": len(rb), "hash": stable_hash(rb[:50])}
        if isinstance(prev_h, dict):
            closer_than_yesterday = len(blockers) < int(prev_h.get("blocker_count") or 999)
            delta = {
                "truth_version": "autonomous_progress_delta_v1",
                "generated_at": _now_iso(),
                "blocker_count_before": prev_h.get("blocker_count"),
                "blocker_count_now": len(blockers),
                "hash_before": prev_h.get("hash"),
                "hash_now": cur_payload["hash"],
                "closer_than_previous_snapshot": closer_than_yesterday,
                "honesty": "Delta compares artifact-derived lists only — not PnL or optimism.",
            }
    else:
        delta = {
            "truth_version": "autonomous_progress_delta_v1",
            "generated_at": _now_iso(),
            "honesty": "No previous autonomous_gap_closer snapshot — run twice to compute delta.",
        }

    gap = {
        "truth_version": "autonomous_gap_closer_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "brutally_honest": True,
        "what_is_missing_for_autonomous_readiness": path_report.get("progression", {}).get("still_missing"),
        "blocker_classification": classified,
        "structural_vs_historical": {
            "structural_runtime": [x for x in classified if x["class"] == "structural_runtime"][:20],
            "historical_or_stale": [x for x in classified if x["class"] == "historical_or_stale"][:20],
        },
        "real_runtime_blockers": blockers[:30],
        "commands_and_cycles_next": path_report.get("exact_next_runtime_steps"),
        "cannot_satisfy_by_code_alone": cannot_code_only,
        "operator_path_excerpt": {
            "can_arm_autonomous_now": path_report.get("can_arm_autonomous_now"),
            "why_not_armable_now": path_report.get("why_not_armable_now"),
        },
        "snapshot": cur_payload,
    }
    write_json_atomic(autonomous_gap_closer_path(root), gap)
    write_json_atomic(autonomous_progress_delta_path(root), delta)

    next_steps = {
        "truth_version": "autonomous_next_steps_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "steps": list(path_report.get("exact_next_runtime_steps") or [])[:20],
        "proof_commands": [
            "python -m trading_ai.deployment autonomous-verification-smoke",
            "python -m trading_ai.deployment write-avenue-a-autonomous-blockers",
            "python -m trading_ai.deployment autonomous-proof-report",
        ],
    }
    write_json_atomic(autonomous_next_steps_path(root), next_steps)

    return {"autonomous_gap_closer": gap, "autonomous_progress_delta": delta, "autonomous_next_steps": next_steps}
