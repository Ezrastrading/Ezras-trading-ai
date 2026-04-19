"""Research → live firewall: strategy ladder + logged promotion/demotion."""

from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.paths import nte_promotion_log_path
from trading_ai.nte.utils.atomic_json import atomic_write_json


class StrategyLiveStatus(str, Enum):
    CANDIDATE = "candidate"
    SANDBOX = "sandbox"
    MICRO_LIVE = "micro_live"
    PROBATIONARY_LIVE = "probationary_live"
    APPROVED_LIVE = "approved_live"
    CORE_LIVE = "core_live"
    REJECTED = "rejected"


LIVE_ROUTING_ALLOWED = frozenset(
    {
        StrategyLiveStatus.APPROVED_LIVE,
        StrategyLiveStatus.CORE_LIVE,
    }
)

# Core Coinbase spot — A/B approved for live when no row exists; C stays sandbox until promoted.
_DEFAULT_CORE_STRATEGIES = frozenset(
    {
        "mean_reversion",
        "continuation_pullback",
    }
)


def _load(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 2, "events": [], "strategies": {}}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"schema_version": 2, "events": [], "strategies": {}}
    except Exception:
        return {"schema_version": 2, "events": [], "strategies": {}}


def get_strategy_status(
    strategy_id: str,
    *,
    path: Optional[Path] = None,
) -> StrategyLiveStatus:
    data = _load(path or nte_promotion_log_path())
    strategies = data.get("strategies") or {}
    if not isinstance(strategies, dict):
        strategies = {}
    raw = strategies.get(strategy_id)
    if raw is None and strategy_id in _DEFAULT_CORE_STRATEGIES:
        return StrategyLiveStatus.APPROVED_LIVE
    if isinstance(raw, dict):
        s = str(raw.get("status") or "candidate")
    else:
        s = str(raw or "candidate")
    try:
        return StrategyLiveStatus(s)
    except ValueError:
        return StrategyLiveStatus.CANDIDATE


def set_strategy_status(
    strategy_id: str,
    status: StrategyLiveStatus,
    *,
    reason: str = "",
    path: Optional[Path] = None,
) -> None:
    p = path or nte_promotion_log_path()
    data = _load(p)
    strategies = dict(data.get("strategies") or {})
    strategies[strategy_id] = {
        "status": status.value,
        "updated_ts": time.time(),
        "reason": reason,
    }
    data["strategies"] = strategies
    events = list(data.get("events") or [])
    events.append(
        {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "strategy_id": strategy_id,
            "event": "status_set",
            "status": status.value,
            "reason": reason,
        }
    )
    data["events"] = events[-2000:]
    atomic_write_json(p, data)


def live_routing_permitted(strategy_id: str, *, path: Optional[Path] = None) -> bool:
    st = get_strategy_status(strategy_id, path=path)
    return st in LIVE_ROUTING_ALLOWED


def promotion_allowed(
    strategy_id: str,
    *,
    passed_checks: bool,
    reviewer: str = "system",
    path: Optional[Path] = None,
) -> bool:
    """Legacy API: log allow/deny; on pass, mark strategy ``approved_live``."""
    p = path or nte_promotion_log_path()
    data = _load(p)
    events = list(data.get("events") or [])
    eid = str(uuid.uuid4())
    events.append(
        {
            "id": eid,
            "ts": time.time(),
            "strategy_id": strategy_id,
            "allowed": passed_checks,
            "reviewer": reviewer,
            "legacy": True,
        }
    )
    data["events"] = events[-2000:]
    if passed_checks:
        strategies = dict(data.get("strategies") or {})
        strategies[strategy_id] = {
            "status": StrategyLiveStatus.APPROVED_LIVE.value,
            "updated_ts": time.time(),
            "reason": "promotion_allowed_passed_checks",
        }
        data["strategies"] = strategies
    atomic_write_json(p, data)
    return passed_checks


def log_demotion(
    strategy_id: str,
    *,
    new_status: StrategyLiveStatus = StrategyLiveStatus.SANDBOX,
    reason: str = "",
    path: Optional[Path] = None,
) -> None:
    """Record demotion (or rejection) in promotion_log."""
    set_strategy_status(strategy_id, new_status, reason=reason or "demotion", path=path)


def assert_live_strategy_or_block(strategy_id: str, *, avenue_id: str = "coinbase") -> None:
    """Raise RuntimeError if strategy is not allowed for normal live routing."""
    if live_routing_permitted(strategy_id):
        return
    from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure

    log_failure(
        FailureClass.SANDBOX_PROMOTION,
        f"Strategy {strategy_id} not approved for live routing on {avenue_id}",
        severity="warning",
        avenue=avenue_id,
        metadata={"strategy_id": strategy_id},
    )
    raise RuntimeError(f"Live routing blocked for strategy={strategy_id}")
