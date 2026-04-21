"""Incident response — structured records + automatic postmortem pointer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.orchestration_paths import incidents_log_path


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_incident(
    classification: str,
    *,
    domain: str,
    caused_by_bot_id: Optional[str],
    severity: str,
    action_taken: str,
    freeze_scope: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    """
    classification: execution_conflict | duplicate_trade | data_corruption | api_failure | policy_breach
    freeze_scope: none | bot | gate | avenue | global_routing
    """
    rec = {
        "truth_version": "incident_v1",
        "at": _iso(),
        "classification": classification,
        "domain": domain,
        "caused_by_bot_id": caused_by_bot_id,
        "severity": severity,
        "action_taken": action_taken,
        "freeze_scope": freeze_scope,
        "postmortem_required": True,
        "details": details,
    }
    p = incidents_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    pm = p.parent / f"postmortem_{classification}_{_iso().replace(':', '-')}.json"
    pm.write_text(json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec
