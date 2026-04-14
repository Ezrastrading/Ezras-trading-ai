"""
Execution intent vs venue result — deterministic reconciliation and audit trail.

State: ``{EZRAS_RUNTIME_ROOT}/state/execution_reconciliation_state.json``
Log: ``{EZRAS_RUNTIME_ROOT}/logs/execution_reconciliation_log.md``
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1

DEFAULT_SIZE_REL_TOLERANCE = 0.02
DEFAULT_PRICE_SLIPPAGE_ABS = 0.02


def _state_path() -> Path:
    return runtime_root() / "state" / "execution_reconciliation_state.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "execution_reconciliation_log.md"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "trades": {}, "last_list": []}


def _load() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("trades", {})
        out.setdefault("last_list", [])
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_state()


def _save(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = _STATE_VERSION
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _append_log(row: Dict[str, Any]) -> None:
    try:
        _log_path().parent.mkdir(parents=True, exist_ok=True)
        ts = row.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = f"\n## {ts}\n\n```json\n{json.dumps(row, indent=2, default=str)}\n```\n\n---\n"
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("execution_reconciliation log append failed: %s", exc)


def _ratio(a: float, b: float) -> float:
    if b == 0:
        return 1.0 if abs(a) < 1e-12 else 0.0
    return a / b


def reconcile_execution_intent_vs_result(
    *,
    trade_id: str,
    requested_size: float,
    approved_size: float,
    submitted_size: float,
    filled_size: float,
    avg_fill_price: Optional[float],
    expected_entry_price: Optional[float],
    fees: Optional[float],
    realized_pnl_if_closed: Optional[float] = None,
    size_rel_tolerance: float = DEFAULT_SIZE_REL_TOLERANCE,
    price_slippage_abs: float = DEFAULT_PRICE_SLIPPAGE_ABS,
) -> Dict[str, Any]:
    """
    Build reconciliation object and verdict. Does not persist (use record_* helpers to persist).
    """
    tid = str(trade_id or "").strip() or "unknown"
    req = float(requested_size)
    appr = float(approved_size)
    sub = float(submitted_size)
    fill = float(filled_size)
    exp_px = float(expected_entry_price) if expected_entry_price is not None else None
    avg_px = float(avg_fill_price) if avg_fill_price is not None else None

    tol = lambda a, b: abs(a - b) <= max(1e-9, abs(b) * 0.001, 0.01)  # noqa: E731
    req_vs_sub = tol(req, sub)
    appr_vs_sub = tol(appr, sub)
    sub_vs_fill = abs(sub - fill) <= max(1e-9, abs(sub) * size_rel_tolerance, 0.01)

    fill_ratio = _ratio(fill, sub) if sub > 0 else (1.0 if fill == 0 else 0.0)

    price_slippage = None
    if exp_px is not None and avg_px is not None:
        price_slippage = abs(avg_px - exp_px)

    fee_missing = fees is None
    issues: List[str] = []
    if not appr_vs_sub:
        issues.append("approved_vs_submitted")
    if not sub_vs_fill:
        issues.append("submitted_vs_filled")
    if not req_vs_sub:
        issues.append("requested_vs_submitted")

    verdict = "CLEAN"
    if not appr_vs_sub:
        verdict = "SIZE_DRIFT"
    elif not sub_vs_fill:
        verdict = "PARTIAL_FILL"
    if price_slippage is not None and price_slippage > price_slippage_abs:
        verdict = "PRICE_DRIFT" if verdict == "CLEAN" else "DISCREPANCY"
    if fee_missing and verdict == "CLEAN":
        verdict = "FEE_DRIFT"
    if len([x for x in issues if x != "requested_vs_submitted"]) > 1 or (
        verdict not in ("CLEAN", "FEE_DRIFT") and len(issues) > 1
    ):
        verdict = "DISCREPANCY"

    requires_review = verdict != "CLEAN" or fee_missing or len(issues) > 0

    rec: Dict[str, Any] = {
        "trade_id": tid,
        "requested_size": round(req, 6),
        "approved_size": round(appr, 6),
        "submitted_size": round(sub, 6),
        "filled_size": round(fill, 6),
        "fill_ratio": round(fill_ratio, 6),
        "requested_vs_submitted_match": bool(req_vs_sub),
        "approved_vs_submitted_match": bool(appr_vs_sub),
        "submitted_vs_filled_match": bool(sub_vs_fill),
        "avg_fill_price": avg_px,
        "expected_entry_price": exp_px,
        "price_slippage": price_slippage,
        "fees": fees,
        "realized_result_if_closed": realized_pnl_if_closed,
        "execution_quality_verdict": verdict,
        "requires_review": requires_review,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return rec


def record_execution_submission(
    *,
    trade_id: str,
    requested_size: float,
    approved_size: float,
    submitted_size: float,
    expected_entry_price: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record submit step; merges into trade record."""
    tid = str(trade_id or "").strip() or "unknown"
    row = {
        "trade_id": tid,
        "requested_size": float(requested_size),
        "approved_size": float(approved_size),
        "submitted_size": float(submitted_size),
        "expected_entry_price": expected_entry_price,
        "phase": "submission",
        "extra": extra or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _merge_trade_row(tid, row)
    _append_log({"event": "submission", **row})
    return row


def record_execution_fill(
    *,
    trade_id: str,
    filled_size: float,
    avg_fill_price: Optional[float],
    fees: Optional[float],
    expected_entry_price: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tid = str(trade_id or "").strip() or "unknown"
    with _lock:
        st = _load()
        cur = dict((st.get("trades") or {}).get(tid) or {})
        req = float(cur.get("requested_size") or cur.get("requested") or 0.0)
        appr = float(cur.get("approved_size") or cur.get("approved") or 0.0)
        sub = float(cur.get("submitted_size") or cur.get("submitted") or appr)
        exp = expected_entry_price if expected_entry_price is not None else cur.get("expected_entry_price")
        rec = reconcile_execution_intent_vs_result(
            trade_id=tid,
            requested_size=req or appr,
            approved_size=appr,
            submitted_size=sub,
            filled_size=float(filled_size),
            avg_fill_price=avg_fill_price,
            expected_entry_price=float(exp) if exp is not None else None,
            fees=fees,
        )
        rec["phase"] = "fill"
        rec["extra"] = extra or {}
        st.setdefault("trades", {})[tid] = {**cur, **rec}
        lst: List[Dict[str, Any]] = list(st.get("last_list") or [])
        lst.append(rec)
        st["last_list"] = lst[-256:]
        st["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _save(st)
        except Exception as exc:
            logger.warning("execution reconciliation save failed: %s", exc)
    _append_log({"event": "fill_reconcile", **rec})
    try:
        from trading_ai.risk.hard_lockouts import update_lockout_state_from_execution_reconciliation

        update_lockout_state_from_execution_reconciliation(rec)
    except Exception as exc:
        logger.warning("lockout hook from reconciliation failed: %s", exc)
    try:
        from trading_ai.ops.exception_dashboard import add_exception_event

        if rec.get("requires_review"):
            add_exception_event(
                category="reconciliation_drift",
                message=f"Execution reconciliation requires review: {rec.get('execution_quality_verdict')}",
                severity="HIGH",
                related_trade_id=tid,
                requires_review=True,
            )
    except Exception as exc:
        logger.warning("exception dashboard hook failed: %s", exc)
    return rec


def record_execution_close(
    *,
    trade_id: str,
    realized_pnl: Optional[float],
    fees_total: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tid = str(trade_id or "").strip() or "unknown"
    with _lock:
        st = _load()
        cur = dict((st.get("trades") or {}).get(tid) or {})
        cur["realized_result_if_closed"] = realized_pnl
        cur["fees"] = fees_total if fees_total is not None else cur.get("fees")
        cur["phase"] = "close"
        cur["timestamp_close"] = datetime.now(timezone.utc).isoformat()
        cur["extra_close"] = extra or {}
        st.setdefault("trades", {})[tid] = cur
        st["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _save(st)
        except Exception as exc:
            logger.warning("execution reconciliation close save failed: %s", exc)
    row = {"event": "close", "trade_id": tid, "realized_pnl": realized_pnl, "fees": fees_total}
    _append_log(row)
    return row


def _merge_trade_row(tid: str, row: Dict[str, Any]) -> None:
    with _lock:
        st = _load()
        cur = dict((st.get("trades") or {}).get(tid) or {})
        cur.update(row)
        st.setdefault("trades", {})[tid] = cur
        st["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _save(st)
        except Exception as exc:
            logger.warning("execution reconciliation merge save failed: %s", exc)


def get_execution_reconciliation_status(*, trade_id: Optional[str] = None) -> Dict[str, Any]:
    st = _load()
    if trade_id:
        tid = str(trade_id).strip()
        return {"ok": True, "trade": (st.get("trades") or {}).get(tid), "runtime_root": str(runtime_root())}
    return {
        "ok": True,
        "version": st.get("version"),
        "trade_count": len(st.get("trades") or {}),
        "last_reconciliations": list(st.get("last_list") or [])[-16:],
        "runtime_root": str(runtime_root()),
        "updated_at": st.get("updated_at"),
    }


def get_last_reconciliation() -> Optional[Dict[str, Any]]:
    st = _load()
    lst = list(st.get("last_list") or [])
    return lst[-1] if lst else None
