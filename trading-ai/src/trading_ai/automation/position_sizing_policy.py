"""
Deterministic position sizing from account risk bucket (NORMAL / REDUCED / BLOCKED).

**Live opens (invariant):** import and call from
``trading_ai.automation.live_trade_open_gate`` — :func:`approve_new_trade_for_execution`
(and :func:`validate_trade_open_invariants` after mutation). Phase 2 uses that façade in
``trade_ops.log_trade``.

State: ``{EZRAS_RUNTIME_ROOT}/state/position_sizing_state.json``
Log: ``{EZRAS_RUNTIME_ROOT}/logs/position_sizing_log.md`` (append-only)
Pre-submit audit (successful live approvals only): ``{EZRAS_RUNTIME_ROOT}/logs/pre_submit_sizing_log.md`` (append-only)
"""

from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.automation.adaptive_sizing import get_effective_sizing_multiplier
from trading_ai.automation.risk_bucket import get_account_risk_bucket, runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 3

# Canonical ``position_sizing_meta`` keys (required for persistence / Telegram).
CANONICAL_META_REQUIRED_KEYS = (
    "requested_size",
    "approved_size",
    "raw_bucket",
    "effective_bucket",
    "bucket_fallback_applied",
    "sizing_multiplier",
    "approval_status",
    "reason",
    "trading_allowed",
    "normalized_at",
    "source",
    "repair_applied",
    "repair_reason",
)


def position_sizing_state_path() -> Path:
    return runtime_root() / "state" / "position_sizing_state.json"


def position_sizing_log_path() -> Path:
    return runtime_root() / "logs" / "position_sizing_log.md"


def pre_submit_sizing_log_path() -> Path:
    """Append-only log of sizing decisions immediately before Phase 2 persistence (successful approvals)."""
    return runtime_root() / "logs" / "pre_submit_sizing_log.md"


def append_pre_submit_sizing_log(trade: Dict[str, Any]) -> None:
    """
    Structured pre-persistence sizing snapshot for live opens (append-only, never overwrites file).

    Called from :func:`approve_new_trade_for_execution` after approval and invariant checks,
    before returning to the caller (e.g. ``log_trade`` → ``save_trades``).
    """
    try:
        meta = trade.get("position_sizing_meta") or {}
        ts = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "timestamp": ts,
            "event_type": "pre_submit_sizing",
            "trade_id": str(trade.get("trade_id") or "").strip() or "unknown",
            "requested_size": meta.get("requested_size"),
            "approved_size": meta.get("approved_size"),
            "raw_bucket": meta.get("raw_bucket"),
            "effective_bucket": meta.get("effective_bucket"),
            "bucket_fallback_applied": bool(meta.get("bucket_fallback_applied")),
            "sizing_multiplier": meta.get("sizing_multiplier"),
            "approval_status": meta.get("approval_status"),
            "trading_allowed": meta.get("trading_allowed"),
            "reason": meta.get("reason"),
        }
        p = pre_submit_sizing_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        block = (
            f"\n## {ts} — pre_submit_sizing — {row['trade_id']}\n\n"
            f"```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        )
        with p.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("append_pre_submit_sizing_log failed: %s", exc)


class TradePlacementBlocked(Exception):
    """New trade rejected by sizing policy (e.g. BLOCKED bucket or invalid size)."""

    def __init__(
        self,
        message: str,
        *,
        decision: Dict[str, Any],
        trade_snapshot: Dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.trade_snapshot = trade_snapshot


def get_sizing_policy_for_bucket(effective_bucket: str) -> Dict[str, Any]:
    """
    Multiplier and trading_allowed for **effective** bucket only
    (NORMAL | REDUCED | BLOCKED). Unknown labels must be normalized before calling.
    """
    b = str(effective_bucket or "").strip().upper()
    if b == "NORMAL":
        return {"bucket": "NORMAL", "sizing_multiplier": 1.0, "trading_allowed": True}
    if b == "REDUCED":
        return {"bucket": "REDUCED", "sizing_multiplier": 0.5, "trading_allowed": True}
    if b == "BLOCKED":
        return {"bucket": "BLOCKED", "sizing_multiplier": 0.0, "trading_allowed": False}
    raise ValueError(f"invalid effective_bucket: {effective_bucket!r}")


def validate_requested_size(value: Any) -> Dict[str, Any]:
    """Return ``{valid, value, reason}`` for requested notional."""
    if value is None:
        return {"valid": False, "value": None, "reason": "missing"}
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"valid": False, "value": None, "reason": "not_numeric"}
    if math.isnan(v) or math.isinf(v):
        return {"valid": False, "value": None, "reason": "not_finite"}
    if v <= 0:
        return {"valid": False, "value": None, "reason": "non_positive"}
    return {"valid": True, "value": v, "reason": "ok"}


def extract_requested_capital(trade: Dict[str, Any]) -> Optional[float]:
    """Prefer ``capital_allocated``, then ``size_dollars``, then ``planned_risk`` if numeric."""
    for key in ("capital_allocated", "size_dollars", "planned_risk"):
        if key in trade and trade[key] is not None:
            vr = validate_requested_size(trade[key])
            if vr["valid"]:
                return float(vr["value"])
    return None


def resolve_raw_and_effective_bucket(trade_event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Read persisted risk bucket and normalize to an **effective** bucket for policy.

    UNKNOWN / invalid / unreadable => effective REDUCED, ``bucket_fallback_applied`` True.
    """
    raw_bucket = "UNKNOWN"
    try:
        b = get_account_risk_bucket(trade_event)
        raw_bucket = str(b).strip().upper() if b is not None else "UNKNOWN"
    except Exception as exc:
        logger.warning("resolve_raw_bucket failed -> UNKNOWN: %s", exc)
        raw_bucket = "UNKNOWN"

    if raw_bucket in ("NORMAL", "REDUCED", "BLOCKED"):
        return {
            "raw_bucket": raw_bucket,
            "effective_bucket": raw_bucket,
            "bucket_fallback_applied": False,
        }
    return {
        "raw_bucket": raw_bucket,
        "effective_bucket": "REDUCED",
        "bucket_fallback_applied": True,
    }


def resolve_bucket_for_policy(trade_event: Optional[Dict[str, Any]] = None) -> str:
    """Backward compat: returns **effective** bucket (never silently NORMAL on bad read)."""
    return str(resolve_raw_and_effective_bucket(trade_event)["effective_bucket"])


def apply_position_sizing_policy(
    requested_size: float,
    effective_bucket: str,
    *,
    raw_bucket: Optional[str] = None,
    bucket_fallback_applied: bool = False,
) -> Dict[str, Any]:
    """
    Pure sizing from validated positive ``requested_size`` and **effective** bucket.
    """
    eff = str(effective_bucket).strip().upper()
    pol = get_sizing_policy_for_bucket(eff)
    mult = float(get_effective_sizing_multiplier(eff))
    allowed = bool(pol["trading_allowed"])

    base: Dict[str, Any] = {
        "requested_size": round(requested_size, 2),
        "raw_bucket": raw_bucket,
        "effective_bucket": eff,
        "bucket_fallback_applied": bool(bucket_fallback_applied),
        "sizing_multiplier": mult,
    }

    if not allowed or mult <= 0:
        return {
            **base,
            "approved_size": 0.0,
            "bucket": eff,
            "approval_status": "BLOCKED",
            "reason": "risk_bucket_blocked",
            "trading_allowed": False,
        }

    approved = round(requested_size * mult + 1e-12, 2)
    if approved <= 0:
        return {
            **base,
            "approved_size": 0.0,
            "bucket": eff,
            "approval_status": "BLOCKED",
            "reason": "rounded_to_zero",
            "trading_allowed": False,
        }

    if eff == "NORMAL" and abs(approved - requested_size) < 0.005:
        status = "APPROVED"
        reason = "risk_bucket_ok"
    elif eff == "REDUCED":
        status = "REDUCED"
        if bucket_fallback_applied:
            reason = "unknown_bucket_failsafe"
        else:
            reason = "risk_bucket_reduction"
    else:
        status = "APPROVED"
        reason = "risk_bucket_ok"

    return {
        **base,
        "approved_size": approved,
        "bucket": eff,
        "approval_status": status,
        "reason": reason,
        "trading_allowed": True,
    }


def apply_position_sizing_policy_safe(
    requested_size: Optional[float],
    effective_bucket: str,
    *,
    raw_bucket: Optional[str] = None,
    bucket_fallback_applied: bool = False,
) -> Dict[str, Any]:
    """Validate size then apply; on validation failure -> BLOCKED."""
    try:
        vr = validate_requested_size(requested_size)
        if not vr["valid"]:
            return {
                "requested_size": None,
                "approved_size": 0.0,
                "raw_bucket": raw_bucket,
                "effective_bucket": str(effective_bucket or "REDUCED"),
                "bucket_fallback_applied": bool(bucket_fallback_applied),
                "sizing_multiplier": 0.0,
                "bucket": str(effective_bucket or "REDUCED"),
                "approval_status": "BLOCKED",
                "reason": "invalid_requested_size",
                "trading_allowed": False,
            }
        return apply_position_sizing_policy(
            float(vr["value"]),
            effective_bucket,
            raw_bucket=raw_bucket,
            bucket_fallback_applied=bucket_fallback_applied,
        )
    except Exception as exc:
        logger.warning("apply_position_sizing_policy_safe BLOCKED: %s", exc)
        return {
            "requested_size": float(requested_size) if requested_size is not None else None,
            "approved_size": 0.0,
            "raw_bucket": raw_bucket,
            "effective_bucket": "REDUCED",
            "bucket_fallback_applied": bool(bucket_fallback_applied),
            "sizing_multiplier": 0.0,
            "bucket": "REDUCED",
            "approval_status": "BLOCKED",
            "reason": "sizing_logic_error",
            "trading_allowed": False,
        }


def meta_is_complete(meta: Any) -> bool:
    """True if ``meta`` has all canonical keys with usable values."""
    if not isinstance(meta, dict):
        return False
    for k in CANONICAL_META_REQUIRED_KEYS:
        if k not in meta:
            return False
    eff = str(meta.get("effective_bucket") or "").upper()
    if eff not in ("NORMAL", "REDUCED", "BLOCKED"):
        return False
    try:
        float(meta.get("approved_size"))
    except (TypeError, ValueError):
        return False
    return True


def validate_trade_open_invariants(
    trade: Dict[str, Any],
    *,
    live: bool = False,
) -> Dict[str, Any]:
    """
    Validate open-trade sizing invariants. ``live=True`` enables stricter checks before persistence.

    Returns ``{ok, errors, repair_hint}``.
    """
    errors: List[str] = []
    meta = trade.get("position_sizing_meta")
    if not isinstance(meta, dict):
        errors.append("missing_position_sizing_meta")
        return {"ok": False, "errors": errors, "repair_hint": "recompute"}

    if not meta_is_complete(meta):
        errors.append("incomplete_position_sizing_meta")

    eff = str(meta.get("effective_bucket") or "").upper()
    try:
        appr = float(meta.get("approved_size"))
    except (TypeError, ValueError):
        errors.append("approved_size_not_numeric")
        appr = -1.0

    if appr < 0:
        errors.append("approved_size_negative")

    st = str(meta.get("approval_status") or "")
    if st and st not in ("APPROVED", "REDUCED", "BLOCKED"):
        errors.append("approval_status_invalid")

    req = meta.get("requested_size")
    if req is not None:
        try:
            rq = float(req)
            if rq > 0 and appr > rq + 0.01:
                errors.append("approved_exceeds_requested")
        except (TypeError, ValueError):
            errors.append("requested_size_invalid")

    if eff == "BLOCKED" and appr > 0.001:
        errors.append("blocked_requires_zero_approved")

    if st == "BLOCKED" and meta.get("trading_allowed") is True:
        errors.append("blocked_requires_trading_disallowed")

    rb = str(meta.get("raw_bucket") or "")
    if rb not in ("NORMAL", "REDUCED", "BLOCKED", "UNKNOWN") and rb:
        errors.append("raw_bucket_unrecognized")

    risk_open = str(trade.get("risk_bucket_at_open") or "").upper()
    if risk_open and risk_open != eff:
        errors.append("risk_bucket_at_open_mismatch")

    try:
        pol = get_sizing_policy_for_bucket(eff)
        exp_m = float(get_effective_sizing_multiplier(eff))
        got_m = float(meta.get("sizing_multiplier"))
        if abs(exp_m - got_m) > 0.001:
            errors.append("multiplier_mismatch")
        if bool(pol["trading_allowed"]) != bool(meta.get("trading_allowed")):
            errors.append("trading_allowed_mismatch")
    except Exception:
        errors.append("policy_lookup_failed")

    if live and not isinstance(meta.get("normalized_at"), str):
        errors.append("missing_normalized_at_live")

    return {"ok": not errors, "errors": errors, "repair_hint": "recompute" if errors else None}


def _build_canonical_meta_from_decision(
    decision: Dict[str, Any],
    *,
    source_path: str,
    repair_applied: bool,
    repair_reason: Optional[str],
) -> Dict[str, Any]:
    eff = str(decision.get("effective_bucket") or decision.get("bucket") or "REDUCED").strip().upper()
    pol = get_sizing_policy_for_bucket(eff)
    mult = float(
        decision.get("sizing_multiplier")
        if decision.get("sizing_multiplier") is not None
        else get_effective_sizing_multiplier(eff)
    )
    ta = decision.get("trading_allowed")
    if ta is None:
        ta = pol["trading_allowed"]
    out = {
        "requested_size": decision.get("requested_size"),
        "approved_size": float(decision.get("approved_size") or 0.0),
        "raw_bucket": decision.get("raw_bucket"),
        "effective_bucket": eff,
        "bucket_fallback_applied": bool(decision.get("bucket_fallback_applied")),
        "sizing_multiplier": mult,
        "approval_status": str(decision.get("approval_status") or "BLOCKED"),
        "reason": str(decision.get("reason") or ""),
        "trading_allowed": bool(ta),
        "normalized_at": datetime.now(timezone.utc).isoformat(),
        "source": source_path,
        "repair_applied": repair_applied,
        "repair_reason": repair_reason,
        "bucket": eff,
    }
    if decision.get("account_risk_bucket") is not None:
        out["account_risk_bucket"] = decision.get("account_risk_bucket")
    if decision.get("strategy_risk_bucket") is not None:
        out["strategy_risk_bucket"] = decision.get("strategy_risk_bucket")
    return out


def normalize_position_sizing_meta(
    trade: Dict[str, Any],
    *,
    source_path: str,
    mutate_capital: bool = False,
    record_audit: bool = False,
) -> Dict[str, Any]:
    """
    Canonicalize ``trade["position_sizing_meta"]``. Missing / partial / invalid meta is repaired
    via shared sizing logic (same as live approval math, without raising on BLOCKED preview).

    Always sets ``risk_bucket_at_open`` = ``effective_bucket``.
    """
    tid = str(trade.get("trade_id") or "").strip() or "unknown"
    existing = trade.get("position_sizing_meta")
    inv = validate_trade_open_invariants(trade, live=False)
    if isinstance(existing, dict) and meta_is_complete(existing) and inv["ok"]:
        eff = str(existing["effective_bucket"])
        trade["risk_bucket_at_open"] = eff
        if mutate_capital and str(existing.get("approval_status")) != "BLOCKED":
            trade["capital_allocated"] = float(existing["approved_size"])
        return dict(existing)

    d = compute_sizing_decision_for_trade(trade, trade_event={"phase": "open", "trade": trade})
    if d.get("trading_allowed") is None:
        pol = get_sizing_policy_for_bucket(str(d.get("effective_bucket") or "REDUCED"))
        d["trading_allowed"] = pol["trading_allowed"]

    repair_reason = "missing_meta"
    if isinstance(existing, dict) and len(existing) > 0:
        repair_reason = "partial_or_invalid_meta"

    meta = _build_canonical_meta_from_decision(
        d,
        source_path=source_path,
        repair_applied=True,
        repair_reason=repair_reason,
    )
    trade["position_sizing_meta"] = meta
    trade["risk_bucket_at_open"] = meta["effective_bucket"]
    if mutate_capital and meta["approval_status"] != "BLOCKED":
        trade["capital_allocated"] = float(meta["approved_size"])

    if record_audit:
        audit = {
            **d,
            "repair_applied": True,
            "repair_reason": repair_reason,
            "source_path": source_path,
            "trading_allowed": meta["trading_allowed"],
        }
        record_position_sizing_decision(
            trade_id=tid,
            decision=audit,
            event_type="preview_open",
            source_path=source_path,
        )
    return meta


def _pack_position_sizing_meta(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Backward compat — use :func:`_build_canonical_meta_from_decision`."""
    return _build_canonical_meta_from_decision(
        decision,
        source_path="live_approval",
        repair_applied=False,
        repair_reason=None,
    )


def compute_sizing_decision_for_trade(
    trade: Dict[str, Any],
    *,
    trade_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Full sizing decision without mutating ``trade``. Used for previews / simulation.
    Does not raise: invalid size -> BLOCKED decision.
    """
    from trading_ai.automation.strategy_risk_bucket import resolve_effective_risk_for_open

    _ = trade_event
    req_raw = extract_requested_capital(trade)
    vr = validate_requested_size(req_raw)
    rb = resolve_effective_risk_for_open(trade)
    raw_b = str(rb["raw_bucket"])
    eff_b = str(rb["effective_bucket"])
    fb = bool(rb["bucket_fallback_applied"])

    if not vr["valid"]:
        return {
            "requested_size": None,
            "approved_size": 0.0,
            "raw_bucket": raw_b,
            "effective_bucket": eff_b,
            "bucket_fallback_applied": fb,
            "sizing_multiplier": 0.0,
            "bucket": eff_b,
            "approval_status": "BLOCKED",
            "reason": "invalid_requested_size",
            "trading_allowed": False,
            "account_risk_bucket": rb.get("account_bucket"),
            "strategy_risk_bucket": rb.get("strategy_bucket"),
        }

    try:
        d = apply_position_sizing_policy(
            float(vr["value"]),
            eff_b,
            raw_bucket=raw_b,
            bucket_fallback_applied=fb,
        )
        d["account_risk_bucket"] = rb.get("account_bucket")
        d["strategy_risk_bucket"] = rb.get("strategy_bucket")
        return d
    except Exception as exc:
        logger.warning("compute_sizing_decision_for_trade: %s", exc)
        return apply_position_sizing_policy_safe(
            vr["value"],
            "REDUCED",
            raw_bucket=raw_b,
            bucket_fallback_applied=True,
        )


def enrich_open_payload_with_sizing_preview(trade: Dict[str, Any]) -> None:
    """
    Post-trade / hub: canonicalize sizing meta for Telegram (never persists Phase 2 trades).

    Does **not** mutate ``capital_allocated``. Always runs :func:`normalize_position_sizing_meta`.
    """
    meta = trade.get("position_sizing_meta") or {}
    skip_audit = meta.get("source") == "live_approval" and meta_is_complete(meta)
    normalize_position_sizing_meta(
        trade,
        source_path="preview_open",
        mutate_capital=False,
        record_audit=not skip_audit,
    )


def approve_new_trade_for_execution(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single source of truth for live new-trade approval before Phase 2 persistence.

    1. Validate requested size fields
    2. Resolve raw + effective account bucket
    3. Apply sizing policy
    4. Attach ``position_sizing_meta`` and ``risk_bucket_at_open`` (effective)
    5. Set ``capital_allocated`` to approved size
    6. Return structured decision

    Raises :class:`TradePlacementBlocked` when the open must not proceed.
    """
    from trading_ai.automation.strategy_risk_bucket import resolve_effective_risk_for_open
    from trading_ai.risk.hard_lockouts import can_open_new_trade

    tid = str(trade.get("trade_id") or "").strip() or "unknown"
    co = can_open_new_trade()
    if not co["allowed"]:
        decision = {
            "requested_size": extract_requested_capital(trade),
            "approved_size": 0.0,
            "raw_bucket": "BLOCKED",
            "effective_bucket": "BLOCKED",
            "bucket_fallback_applied": False,
            "sizing_multiplier": 0.0,
            "bucket": "BLOCKED",
            "approval_status": "BLOCKED",
            "reason": "hard_lockout_active",
            "trading_allowed": False,
            "lockout_reasons": co.get("reasons"),
        }
        record_position_sizing_decision(
            trade_id=tid,
            decision=decision,
            event_type="rejected_hard_lockout",
            source_path="live_open",
        )
        try:
            from trading_ai.ops.exception_dashboard import add_exception_event

            add_exception_event(
                category="lockout_active",
                message="Open blocked: " + "; ".join(co.get("reasons") or ["lockout"]),
                severity="CRITICAL",
                related_trade_id=tid,
                requires_review=True,
            )
        except Exception:
            pass
        snap = {k: trade.get(k) for k in ("trade_id", "market", "position", "timestamp", "entry_price")}
        raise TradePlacementBlocked(
            "hard_lockout_active",
            decision=decision,
            trade_snapshot=snap,
        )

    req_raw = extract_requested_capital(trade)
    vr = validate_requested_size(req_raw)
    rb = resolve_effective_risk_for_open(trade)
    raw_b = str(rb["raw_bucket"])
    eff_b = str(rb["effective_bucket"])
    fb = bool(rb["bucket_fallback_applied"])

    if not vr["valid"]:
        decision = {
            "requested_size": None,
            "approved_size": 0.0,
            "raw_bucket": raw_b,
            "effective_bucket": eff_b,
            "bucket_fallback_applied": fb,
            "sizing_multiplier": 0.0,
            "bucket": eff_b,
            "approval_status": "BLOCKED",
            "reason": "invalid_requested_size",
            "account_risk_bucket": rb.get("account_bucket"),
            "strategy_risk_bucket": rb.get("strategy_bucket"),
        }
        record_position_sizing_decision(
            trade_id=tid,
            decision={**decision, "trading_allowed": False},
            event_type="rejected_invalid_size",
            source_path="live_open",
        )
        snap = {k: trade.get(k) for k in ("trade_id", "market", "position", "timestamp", "entry_price")}
        raise TradePlacementBlocked(
            "invalid_requested_size: capital_allocated / size_dollars / planned_risk",
            decision=decision,
            trade_snapshot=snap,
        )

    try:
        decision = apply_position_sizing_policy(
            float(vr["value"]),
            eff_b,
            raw_bucket=raw_b,
            bucket_fallback_applied=fb,
        )
        decision["account_risk_bucket"] = rb.get("account_bucket")
        decision["strategy_risk_bucket"] = rb.get("strategy_bucket")
    except Exception as exc:
        logger.warning("approve_new_trade_for_execution apply error: %s", exc)
        decision = apply_position_sizing_policy_safe(
            vr["value"],
            "REDUCED",
            raw_bucket=raw_b,
            bucket_fallback_applied=True,
        )
        decision["approval_status"] = "BLOCKED"
        decision["reason"] = "sizing_logic_error"
        decision["account_risk_bucket"] = rb.get("account_bucket")
        decision["strategy_risk_bucket"] = rb.get("strategy_bucket")

    if str(decision.get("approval_status")) == "BLOCKED" or float(decision.get("approved_size") or 0) <= 0:
        record_position_sizing_decision(
            trade_id=tid,
            decision=decision,
            event_type="rejected_blocked",
            source_path="live_open",
        )
        snap = {k: trade.get(k) for k in ("trade_id", "market", "position", "timestamp", "entry_price")}
        raise TradePlacementBlocked(
            str(decision.get("reason") or "blocked"),
            decision=decision,
            trade_snapshot=snap,
        )

    trade["capital_allocated"] = float(decision["approved_size"])
    trade["position_sizing_meta"] = _pack_position_sizing_meta(decision)
    trade["risk_bucket_at_open"] = str(decision.get("effective_bucket") or decision.get("bucket"))
    inv_live = validate_trade_open_invariants(trade, live=True)
    if not inv_live["ok"]:
        raise RuntimeError(f"trade_open_invariant_failure_after_approval: {inv_live['errors']}")
    record_position_sizing_decision(
        trade_id=tid,
        decision=decision,
        event_type="log_trade_approved",
        source_path="live_open",
    )
    append_pre_submit_sizing_log(trade)
    return decision


def enforce_position_sizing_on_trade_dict(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Deprecated name — calls :func:`approve_new_trade_for_execution`."""
    return approve_new_trade_for_execution(trade)


def _default_sizing_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "last_event": None}


def read_position_sizing_state() -> Dict[str, Any]:
    p = position_sizing_state_path()
    if not p.is_file():
        return _default_sizing_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_sizing_state()
        out = _default_sizing_state()
        out.update(raw)
        out.setdefault("last_event", None)
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("read_position_sizing_state corrupt; resetting template: %s", exc)
        return _default_sizing_state()


def write_position_sizing_state(last_event: Dict[str, Any]) -> None:
    """Atomic replace of state file with latest decision summary."""
    with _lock:
        payload = read_position_sizing_state()
        payload["version"] = _STATE_VERSION
        payload["last_event"] = last_event
        payload["last_decision_full"] = dict(last_event)
        payload["last_repair_applied"] = bool(last_event.get("repair_applied"))
        payload["last_repair_reason"] = last_event.get("repair_reason")
        payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        p = position_sizing_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(p)


def append_position_sizing_log(entry: Dict[str, Any]) -> None:
    """Append structured JSON block (never overwrites)."""
    try:
        p = position_sizing_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
        row = dict(entry)
        row.setdefault("timestamp", ts)
        block = (
            f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        )
        with p.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("append_position_sizing_log failed: %s", exc)


def record_position_sizing_decision(
    *,
    trade_id: str,
    decision: Dict[str, Any],
    event_type: str = "log_trade",
    source_path: Optional[str] = None,
) -> None:
    """Persist last state + append log line (audit). Append-only."""
    ev = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trade_id": trade_id,
        "event_type": event_type,
        "source_path": source_path or decision.get("source_path") or "unspecified",
        "requested_size": decision.get("requested_size"),
        "approved_size": decision.get("approved_size"),
        "raw_bucket": decision.get("raw_bucket"),
        "effective_bucket": decision.get("effective_bucket") or decision.get("bucket"),
        "bucket_fallback_applied": bool(decision.get("bucket_fallback_applied")),
        "repair_applied": bool(decision.get("repair_applied")),
        "repair_reason": decision.get("repair_reason"),
        "multiplier": decision.get("sizing_multiplier"),
        "approval_status": decision.get("approval_status"),
        "trading_allowed": decision.get("trading_allowed"),
        "reason": decision.get("reason"),
    }
    append_position_sizing_log(ev)
    try:
        write_position_sizing_state(ev)
    except Exception as exc:
        logger.warning("write_position_sizing_state failed: %s", exc)


def maybe_notify_trade_blocked_by_sizing(exc: TradePlacementBlocked) -> None:
    """Best-effort Telegram for blocked placement; never raises."""
    try:
        from trading_ai.automation.telegram_trade_events import format_trade_sizing_blocked_alert
        from trading_ai.automation.telegram_ops import send_telegram_with_idempotency
        from trading_ai.config import get_settings

        settings = get_settings()
        text = format_trade_sizing_blocked_alert(exc.trade_snapshot, exc.decision)
        tid = str(exc.trade_snapshot.get("trade_id") or "unknown")
        req = exc.decision.get("requested_size")
        dk = f"blocked:{tid}:{req}"
        send_telegram_with_idempotency(
            settings,
            text,
            dedupe_key=dk,
            event_label="trade_blocked_sizing",
        )
    except Exception as e:
        logger.warning("maybe_notify_trade_blocked_by_sizing failed: %s", e)


def sizing_status_snapshot() -> Dict[str, Any]:
    """CLI / inspection: raw vs effective bucket, policy, last state."""
    from trading_ai.automation.strategy_risk_bucket import resolve_effective_risk_for_open

    rb = resolve_raw_and_effective_bucket(None)
    try:
        rx = resolve_effective_risk_for_open({})
    except Exception:
        rx = {}
    eff = str(rb["effective_bucket"])
    pol = get_sizing_policy_for_bucket(eff)
    st = read_position_sizing_state()
    return {
        "raw_bucket": rb["raw_bucket"],
        "effective_bucket": eff,
        "bucket_fallback_applied": rb["bucket_fallback_applied"],
        "account_risk_bucket_effective": eff,
        "strategy_risk_preview": rx.get("strategy_bucket"),
        "effective_risk_preview": rx.get("effective_bucket"),
        "policy": pol,
        "trading_allowed": bool(pol.get("trading_allowed")),
        "current_multiplier": float(pol.get("sizing_multiplier") or 0.0),
        "position_sizing_state": st,
        "last_decision_full": st.get("last_decision_full") or st.get("last_event"),
        "last_repair_applied": st.get("last_repair_applied"),
        "last_repair_reason": st.get("last_repair_reason"),
        "last_decision_summary": st.get("last_event"),
        "runtime_root": str(runtime_root()),
    }


def simulate_sizing_cli(
    requested_size: float,
    bucket_arg: str,
    *,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    CLI ``sizing simulate``: ``bucket_arg`` may be NORMAL|REDUCED|BLOCKED|UNKNOWN.
    UNKNOWN uses the same failsafe as unreadable runtime state.
    """
    _ = trade_id
    b = str(bucket_arg or "").strip().upper()
    if b == "UNKNOWN":
        raw_bucket = "UNKNOWN"
        effective_bucket = "REDUCED"
        bucket_fallback_applied = True
    elif b in ("NORMAL", "REDUCED", "BLOCKED"):
        raw_bucket = b
        effective_bucket = b
        bucket_fallback_applied = False
    else:
        raw_bucket = b
        effective_bucket = "REDUCED"
        bucket_fallback_applied = True

    vr = validate_requested_size(requested_size)
    if not vr["valid"]:
        return {
            "requested_size": requested_size,
            "approved_size": 0.0,
            "raw_bucket": raw_bucket,
            "effective_bucket": effective_bucket,
            "bucket_fallback_applied": bucket_fallback_applied,
            "sizing_multiplier": 0.0,
            "approval_status": "BLOCKED",
            "reason": "invalid_requested_size",
        }

    d = apply_position_sizing_policy(
        float(vr["value"]),
        effective_bucket,
        raw_bucket=raw_bucket,
        bucket_fallback_applied=bucket_fallback_applied,
    )
    out = dict(d)
    out["raw_bucket"] = raw_bucket
    out["effective_bucket"] = effective_bucket
    out["bucket_fallback_applied"] = bucket_fallback_applied
    out["repair_applied"] = bucket_fallback_applied and raw_bucket not in ("NORMAL", "REDUCED", "BLOCKED")
    out["repair_reason"] = "unknown_bucket_failsafe" if out["repair_applied"] else None
    return out
