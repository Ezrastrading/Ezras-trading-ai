"""Typed shapes for deployment checklist and proof runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CheckResult:
    ok: bool
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "details": self.details}


def checklist_checks_template() -> Dict[str, Any]:
    return {
        "exchange_auth_ok": {"ok": False, "reason": "not_run", "details": {}},
        "governance_env_ok": {"ok": False, "reason": "not_run", "details": {}},
        "governance_trading_permitted_ok": {"ok": False, "reason": "not_run", "details": {}},
        "supabase_ok": {"ok": False, "reason": "not_run", "details": {}},
        "supabase_schema_ok": {"ok": False, "reason": "not_run", "details": {}},
        "deployment_parity_ok": {"ok": False, "reason": "not_run", "details": {}},
        "reconciliation_ok": {"ok": False, "reason": "not_run", "details": {}},
        "validation_streak_ready": {"ok": False, "reason": "not_run", "details": {}},
        "first_20_protocol_ready": {"ok": False, "reason": "not_run", "details": {}},
        "env_parity_ok": {"ok": False, "reason": "not_run", "details": {}},
        "soak_ready": {"ok": False, "reason": "not_run", "details": {}},
        "observability_ok": {"ok": False, "reason": "not_run", "details": {}},
    }
