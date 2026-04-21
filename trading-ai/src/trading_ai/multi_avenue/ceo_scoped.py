"""Scoped CEO session bundles — wrappers; legacy global CEO review unchanged."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trading_ai.multi_avenue.namespace_model import SessionScope


def build_scoped_ceo_session_bundle(
    *,
    session_scope: str,
    avenue_id: Optional[str] = None,
    gate_id: Optional[str] = None,
    cross_avenue: bool = False,
) -> Dict[str, Any]:
    """
    Explicitly scoped CEO bundle template. Does not duplicate full CEO narrative logic per venue yet.

    For ``system_wide``, points operators at existing ``ceo_daily_review`` paths.
    For avenue/gate, provides shell + isolation keys for future dual-LLM or file workflows.
    """
    base: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_scope": session_scope,
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "cross_avenue": cross_avenue,
        "contamination_guard": "payloads_must_include_avenue_id_and_gate_id_when_not_system_wide",
    }
    if session_scope == SessionScope.SYSTEM_WIDE.value:
        base["legacy_artifacts"] = {
            "ceo_daily_review_json": "data/review/ceo_daily_review.json",
            "daily_diagnosis_json": "data/review/daily_diagnosis.json",
        }
        base["note"] = "System-wide CEO uses existing diagnosis → CEO pipeline; unchanged."
        return base

    if session_scope == SessionScope.CROSS_AVENUE.value:
        base["note"] = "Cross-avenue CEO must aggregate only labeled rows — never merge raw trade blobs."
        return base

    base["scoped_paths"] = {
        "ceo_bundle_json": f"data/review/avenues/{avenue_id}/ceo_session.json",
        "gate_ceo_bundle_json": f"data/review/avenues/{avenue_id}/gates/{gate_id}/ceo_session.json"
        if gate_id
        else None,
    }
    base["note"] = (
        "Scoped CEO shell — populate from avenue-filtered diagnosis + edge summaries when ready. "
        "No LLM orchestration implied."
    )
    return base
