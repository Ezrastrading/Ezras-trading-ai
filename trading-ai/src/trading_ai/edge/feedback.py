"""Append structured feedback for research refinement (GPT/Claude consumers)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from trading_ai.edge.paths import edge_feedback_log_path


def append_edge_feedback(record: Mapping[str, Any]) -> None:
    path = edge_feedback_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(record)
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def feedback_from_evaluation(edge_id: str, report: Mapping[str, Any]) -> None:
    append_edge_feedback(
        {
            "kind": "edge_evaluation",
            "edge_id": edge_id,
            "promote_to": report.get("promote_to"),
            "reject": report.get("reject"),
            "reasons": report.get("reasons"),
            "metrics": report.get("metrics_dict"),
        }
    )
