"""
Minimal honest stubs for Avenue A switch_live — no fake confirmation, no false go/no-go.

- operator_live_confirmation.json: created only if missing; confirmed stays false until operator sets true.
- go_no_go_decision.json: created only if missing; ready_for_first_5_trades null = not reviewed (does NOT block; only explicit false blocks).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def ensure_minimal_prelive_artifacts(*, runtime_root: Path) -> Dict[str, Any]:
    """Idempotent; never overwrites existing operator or go/no-go files."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    out: Dict[str, Any] = {"operator_template_written": False, "go_no_go_written": False}

    op_rel = "data/control/operator_live_confirmation.json"
    if not ad.exists(op_rel):
        payload = {
            "schema_version": 1,
            "confirmed": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "note": "Set confirmed to true only after explicit operator acknowledgment of live capital risk. "
            "Alternate: EZRAS_OPERATOR_LIVE_CONFIRMED=1 in the process environment (see switch_live._operator_confirmed).",
        }
        ad.write_json(op_rel, payload)
        out["operator_template_written"] = True

    gng_rel = "data/control/go_no_go_decision.json"
    if not ad.exists(gng_rel):
        payload = {
            "schema_version": 1,
            "ready_for_first_5_trades": None,
            "honesty": "null means not reviewed. switch_live only blocks Avenue A when this field is explicitly false.",
            "operator_review_required": True,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        ad.write_json(gng_rel, payload)
        out["go_no_go_written"] = True

    return out
