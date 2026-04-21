"""
Evidence-first morning review readiness truth for Avenue A.

This does not claim 'review ready' unless the required evidence artifacts are present/fresh.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_VERSION = "morning_review_readiness_truth_v1"
_REL = "data/control/morning_review_readiness_truth.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(ad: LocalStorageAdapter, rel: str) -> Dict[str, Any]:
    try:
        j = ad.read_json(rel)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _age_sec(p: Path) -> Optional[float]:
    try:
        if not p.is_file():
            return None
        return max(0.0, (datetime.now(timezone.utc).timestamp() - p.stat().st_mtime))
    except Exception:
        return None


def build_morning_review_readiness_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = LocalStorageAdapter(runtime_root=root)

    # Evidence inputs
    review_packet_path = root / "shark" / "memory" / "global" / "review_packet_latest.json"
    lessons_truth_path = root / "data" / "control" / "lessons_runtime_truth.json"
    ceo_truth_path = root / "data" / "control" / "ceo_session_truth.json"
    loop_path = root / "data" / "control" / "universal_execution_loop_proof.json"
    coord_path = root / "data" / "control" / "avenue_a_coordination_truth.json"

    review_age = _age_sec(review_packet_path)
    lessons_age = _age_sec(lessons_truth_path)
    ceo_age = _age_sec(ceo_truth_path)
    loop_age = _age_sec(loop_path)
    coord_age = _age_sec(coord_path)

    # Load small payloads (for pointers only)
    coord = _read(ad, "data/control/avenue_a_coordination_truth.json")
    loop = _read(ad, "data/control/universal_execution_loop_proof.json")
    lessons = _read(ad, "data/control/lessons_runtime_truth.json")
    ceo = _read(ad, "data/control/ceo_session_truth.json")

    # Freshness policy: review is "ready" if evidence exists and is not stale.
    max_age = 24 * 3600.0
    missing: list[str] = []
    if review_age is None or review_age > max_age:
        missing.append("review_packet_latest_missing_or_stale")
    if lessons_age is None or lessons_age > max_age:
        missing.append("lessons_runtime_truth_missing_or_stale")
    if loop_age is None or loop_age > max_age:
        missing.append("universal_execution_loop_proof_missing_or_stale")
    if coord_age is None or coord_age > max_age:
        missing.append("avenue_a_coordination_truth_missing_or_stale")
    # CEO session truth is helpful but not always a hard blocker; classify separately.
    ceo_ok = ceo_age is not None and ceo_age <= max_age and bool(ceo.get("truth_version"))

    ready = len(missing) == 0

    return {
        "truth_version": _TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "morning_review_ready": bool(ready),
        "missing_or_stale_requirements": missing,
        "freshness": {
            "max_age_sec": max_age,
            "review_packet_age_sec": review_age,
            "lessons_runtime_truth_age_sec": lessons_age,
            "ceo_session_truth_age_sec": ceo_age,
            "universal_execution_loop_proof_age_sec": loop_age,
            "avenue_a_coordination_truth_age_sec": coord_age,
        },
        "latest_trade_context": {
            "last_trade_id": loop.get("last_trade_id"),
            "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN"),
            "last_cycle_trade_id": ((coord.get("trade") or {}).get("trade_id") if isinstance(coord.get("trade"), dict) else None),
            "last_cycle_gate": ((coord.get("trade") or {}).get("gate") if isinstance(coord.get("trade"), dict) else None),
        },
        "support_freshness": {
            "ceo_session_truth_present": bool(ceo.get("truth_version")),
            "ceo_session_truth_ok": bool(ceo_ok),
        },
        "truth_sources": {
            "review_packet_latest": str(review_packet_path),
            "lessons_runtime_truth": "data/control/lessons_runtime_truth.json",
            "ceo_session_truth": "data/control/ceo_session_truth.json",
            "universal_execution_loop_proof": "data/control/universal_execution_loop_proof.json",
            "avenue_a_coordination_truth": "data/control/avenue_a_coordination_truth.json",
        },
        "honesty": "Readiness is evidence-first: requires present+fresh artifacts; does not infer missing data.",
    }


def write_morning_review_readiness_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    payload = build_morning_review_readiness_truth(runtime_root=root)
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_REL, payload)
    ad.write_text(_REL.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    return payload

