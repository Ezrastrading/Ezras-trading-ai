"""
Bounded joint-review → order gating. **Default: advisory only** (no blocking).

See module docstring in prior revision for fail-open vs fail-closed matrix.

All decisions emit a single structured **governance_gate_decision** log line (JSON) when
``check_new_order_allowed_full`` is used — required for runtime proof and bypass audits.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _enforcement_enabled() -> bool:
    """Reads ``os.environ`` on every call — no cached default."""
    return (os.environ.get("GOVERNANCE_ORDER_ENFORCEMENT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _caution_block_entries() -> bool:
    """When true, live_mode ``caution`` blocks new entries under enforcement."""
    return (os.environ.get("GOVERNANCE_CAUTION_BLOCK_ENTRIES") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def governance_enforcement_active() -> bool:
    """True when real order submission may be blocked by joint review (tests / audits)."""
    return _enforcement_enabled()


def _stale_hours() -> float:
    raw = (os.environ.get("GOVERNANCE_JOINT_STALE_HOURS") or "168").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 168.0


def _parse_generated_at(joint: Dict[str, Any]) -> Optional[float]:
    raw = joint.get("generated_at")
    if not raw:
        return None
    s = str(raw).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def load_joint_review_snapshot() -> Dict[str, Any]:
    """Normalized joint review facts for gates and logging."""
    try:
        from trading_ai.global_layer.global_memory_store import GlobalMemoryStore

        st = GlobalMemoryStore()
        j = st.load_json("joint_review_latest.json")
    except Exception as exc:
        logger.warning("governance gate: could not load joint_review_latest: %s", exc)
        return {
            "present": False,
            "empty": True,
            "live_mode": "unknown",
            "integrity": "unknown",
            "stale": False,
            "age_hours": None,
            "joint_review_id": None,
            "packet_id": None,
            "raw": {},
        }

    if not isinstance(j, dict):
        j = {}

    empty = bool(j.get("empty")) or not str(j.get("joint_review_id") or "").strip()
    mode = str(j.get("live_mode_recommendation") or "unknown").strip().lower()
    if mode not in ("normal", "caution", "paused"):
        mode = "unknown"

    integrity = str(j.get("review_integrity_state") or "unknown").strip().lower()

    ts = _parse_generated_at(j)
    age_hours: Optional[float] = None
    stale = False
    if ts is not None:
        age_sec = time.time() - ts
        age_hours = age_sec / 3600.0
        stale = age_hours > _stale_hours()

    return {
        "present": True,
        "empty": empty,
        "live_mode": mode,
        "integrity": integrity,
        "stale": stale,
        "age_hours": age_hours,
        "joint_review_id": str(j.get("joint_review_id") or "") or None,
        "packet_id": str(j.get("packet_id") or "") or None,
        "raw": j,
    }


def load_joint_review_live_mode() -> Tuple[str, Dict[str, Any]]:
    snap = load_joint_review_snapshot()
    raw = snap.get("raw") if isinstance(snap.get("raw"), dict) else {}
    return str(snap.get("live_mode") or "unknown"), raw


def _joint_invalid_under_strict_enforcement(snap: Dict[str, Any]) -> bool:
    """True when joint snapshot is not acceptable for live submission under enforcement."""
    if not snap.get("present") or bool(snap.get("empty")):
        return True
    if snap.get("stale"):
        return True
    integ = str(snap.get("integrity") or "").strip().lower()
    if integ != "full":
        return True
    mode = str(snap.get("live_mode") or "unknown").strip().lower()
    if mode == "paused":
        return True
    if mode == "caution":
        return _caution_block_entries()
    if mode == "unknown":
        return True
    if mode != "normal":
        return True
    return False


def _decide(
    snap: Dict[str, Any],
) -> Tuple[bool, str]:
    """Core decision from snapshot (no logging)."""
    if not _enforcement_enabled():
        return True, "advisory_only_enforcement_disabled"

    # GOVERNANCE_ORDER_ENFORCEMENT=true → absolute fail-closed on invalid joint (no env toggles, no fail-open).
    if not snap.get("present") or snap.get("empty"):
        return False, "missing_joint_fail_closed"

    if snap.get("stale"):
        return False, "stale_joint_fail_closed"

    integrity = str(snap.get("integrity") or "").strip().lower()
    if integrity != "full":
        return False, "degraded_or_unknown_integrity_fail_closed"

    mode = str(snap.get("live_mode") or "unknown").strip().lower()
    caution_block = _caution_block_entries()
    print("governance_mode:", mode, "caution_block:", caution_block)

    if mode == "paused":
        return False, "joint_review_paused"
    if mode == "caution":
        if caution_block:
            return False, "joint_review_caution_blocked"
        return True, "joint_review_caution_allowed"
    if mode == "unknown":
        return False, "unknown_live_mode_fail_closed"
    if mode != "normal":
        return False, f"joint_live_mode_blocked:{mode}"

    return True, "joint_review_normal"


def check_new_order_allowed_full(
    *,
    venue: str,
    operation: str = "new_entry",
    route: str = "n/a",
    intent_id: Optional[str] = None,
    packet_id: Optional[str] = None,
    log_decision: bool = True,
    strategy_class: Optional[str] = None,
    route_bucket: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (allowed, reason_code, audit).

    ``audit`` is suitable for logs and tests; includes joint snapshot fields and decision.
    ``strategy_class`` / ``route_bucket`` are **metadata only** for logs — they do not change the gate math.
    """
    snap = load_joint_review_snapshot()
    ok, reason = _decide(snap)

    # Fail-closed invariant: enforcement on + invalid joint must never yield allowed=True.
    if _enforcement_enabled() and _joint_invalid_under_strict_enforcement(snap) and ok:
        raise AssertionError(
            "FAIL-CLOSED VIOLATION: enforcement enabled but decision allowed=True with invalid joint snapshot"
        )

    audit: Dict[str, Any] = {
        "ts": time.time(),
        "venue": venue,
        "operation": operation,
        "route": route,
        "intent_id": intent_id,
        "enforcement_enabled": _enforcement_enabled(),
        "joint_review_id": snap.get("joint_review_id"),
        "packet_id_ref": packet_id or snap.get("packet_id"),
        "live_mode": snap.get("live_mode"),
        "review_integrity_state": snap.get("integrity"),
        "joint_empty": snap.get("empty"),
        "joint_stale": snap.get("stale"),
        "joint_age_hours": snap.get("age_hours"),
        "allowed": ok,
        "reason_code": reason,
    }
    if strategy_class is not None:
        audit["strategy_class"] = strategy_class
    if route_bucket is not None:
        audit["route_bucket"] = route_bucket

    if log_decision:
        logger.info("governance_gate_decision %s", json.dumps(audit, default=str))

    if not _enforcement_enabled():
        return True, reason, audit

    return ok, reason, audit


def check_new_order_allowed(
    *,
    venue: str,
    operation: str = "new_entry",
    route: str = "n/a",
    packet_id: Optional[str] = None,
) -> Tuple[bool, str]:
    ok, reason, _ = check_new_order_allowed_full(
        venue=venue,
        operation=operation,
        route=route,
        packet_id=packet_id,
        log_decision=True,
    )
    return ok, reason
