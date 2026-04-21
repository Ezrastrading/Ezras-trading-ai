"""Future avenues/gates inherit universal intelligence scaffolding — execution stays separate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.intelligence.paths import (
    scoped_intelligence_learning_dir,
    scoped_intelligence_review_dir,
    scoped_ticket_pointers_json,
    scoped_tickets_root,
)


def ensure_intelligence_scope_for_avenue(
    avenue_id: str,
    *,
    gate_id: Optional[str] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create scoped directories and pointer file so new avenues auto-inherit:
    ticket mirror path, learning/review hooks, daily pointers — without copying execution code.
    """
    st = scoped_tickets_root(avenue_id, runtime_root=runtime_root)
    learn = scoped_intelligence_learning_dir(avenue_id, runtime_root=runtime_root)
    rev = scoped_intelligence_review_dir(avenue_id, runtime_root=runtime_root)
    ptr = scoped_ticket_pointers_json(avenue_id, runtime_root=runtime_root)
    payload: Dict[str, Any] = {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "scoped_tickets_jsonl": str(st / "tickets.jsonl"),
        "scoped_learning_dir": str(learn),
        "scoped_review_dir": str(rev),
        "inheritance_note": (
            "Universal intelligence layer paths are scoped; execution modules are not auto-copied."
        ),
    }
    ptr.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def intelligence_inheritance_manifest(avenue_id: str, *, gate_id: Optional[str] = None) -> Dict[str, Any]:
    """Pure metadata for tests — no I/O."""
    return {
        "avenue_id": avenue_id,
        "gate_id": gate_id,
        "layers": [
            "scoped_ticket_storage",
            "scoped_ceo_review_pointers",
            "scoped_learning_references",
            "scoped_domain_attachment",
            "scoped_daily_review_pointers",
            "scoped_market_research_queue_placeholder",
            "scoped_scanner_placeholder",
            "scoped_status_audit_rows",
        ],
        "execution_not_inherited": True,
    }
