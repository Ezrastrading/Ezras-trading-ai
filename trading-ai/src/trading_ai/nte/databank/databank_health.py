"""Surface databank health from local verification + last pipeline outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.databank.local_trade_store import load_aggregate, path_databank_health, save_aggregate
from trading_ai.nte.databank.trade_verification_engine import load_verification_state


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_health() -> Dict[str, Any]:
    default = {"status": "ok", "issues": [], "updated": None}
    return load_aggregate(path_databank_health(), default)


def save_health(status: str, issues: List[str], extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    data = {
        "status": status,
        "issues": list(issues),
        "updated": _iso(),
    }
    if extra:
        data["extra"] = dict(extra)
    save_aggregate(path_databank_health(), data)
    return data


def refresh_health_from_verification() -> Dict[str, Any]:
    """Mark unhealthy if last verification had partial_failure."""
    v = load_verification_state()
    last = v.get("last")
    issues: List[str] = []
    status = "ok"
    if isinstance(last, dict) and last.get("partial_failure"):
        status = "degraded"
        issues.append(f"last_trade_partial_failure:{last.get('trade_id')}")
        for e in last.get("errors") or []:
            issues.append(str(e))
    return save_health(status, issues, extra={"last_verification": last})
