"""
Internal vs external (venue) truth — strict adapter contract, Kalshi scaffold, explicit verdicts.

State: ``{EZRAS_RUNTIME_ROOT}/state/venue_truth_state.json``
Log: ``{EZRAS_RUNTIME_ROOT}/logs/venue_truth_sync_log.md``

Verdicts: ALIGNED | MINOR_DRIFT | MATERIAL_DRIFT | UNSUPPORTED | ERROR
"""

from __future__ import annotations

import json
import logging
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 2

# --- Adapter response schema (strict validation for truth sync) ---


def validate_external_position_row(row: Any) -> tuple[bool, str]:
    if not isinstance(row, dict):
        return False, "position_not_object"
    tid = row.get("trade_id") or row.get("id") or row.get("ticker")
    if tid is None or str(tid).strip() == "":
        return False, "missing_trade_id"
    return True, "ok"


def normalize_external_open_positions(raw_list: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_list, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in raw_list:
        ok, _ = validate_external_position_row(row)
        if not ok or not isinstance(row, dict):
            continue
        tid = str(row.get("trade_id") or row.get("id") or row.get("ticker") or "").strip()
        out.append(
            {
                "trade_id": tid,
                "id": tid,
                "contracts": float(row.get("contracts") or row.get("position") or row.get("count") or 0),
            }
        )
    return out


class VenueTruthAdapter(Protocol):
    """Strict contract: each fetch returns JSON-serializable structures."""

    def adapter_id(self) -> str: ...
    def fetch_external_open_positions(self) -> List[Dict[str, Any]]: ...
    def fetch_external_recent_fills(self) -> List[Dict[str, Any]]: ...
    def fetch_external_cash_state(self) -> Dict[str, Any]: ...
    def fetch_external_fees(self) -> Dict[str, Any]: ...


class MockLocalVenueAdapter:
    """Deterministic mock for tests and dry verification."""

    def __init__(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        self.snapshot = snapshot or {
            "open_positions": [],
            "recent_fills": [],
            "cash": {"available": 0.0, "currency": "USD"},
            "fees": {"total": 0.0},
        }

    def adapter_id(self) -> str:
        return "mock_local"

    def fetch_external_open_positions(self) -> List[Dict[str, Any]]:
        raw = list(self.snapshot.get("open_positions") or [])
        return normalize_external_open_positions(raw)

    def fetch_external_recent_fills(self) -> List[Dict[str, Any]]:
        return list(self.snapshot.get("recent_fills") or [])

    def fetch_external_cash_state(self) -> Dict[str, Any]:
        return dict(self.snapshot.get("cash") or {})

    def fetch_external_fees(self) -> Dict[str, Any]:
        return dict(self.snapshot.get("fees") or {})


class KalshiVenueTruthAdapter:
    """
    Kalshi Trade API — uses :func:`trading_ai.clients.kalshi.list_portfolio_positions`
    and :func:`trading_ai.clients.kalshi.get_balance` when credentials and ``kalshi_enabled`` allow.

    If credentials are missing or HTTP fails, methods return empty structures; :func:`run_truth_sync`
    promotes that to ``UNSUPPORTED`` or ``ERROR`` with explicit ``detail``.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._last_error: Optional[str] = None

    def adapter_id(self) -> str:
        return "kalshi"

    def fetch_external_open_positions(self) -> List[Dict[str, Any]]:
        from trading_ai.clients import kalshi as kalshi_client

        self._last_error = None
        pr = kalshi_client.list_portfolio_positions(self._settings)
        if not pr.get("ok"):
            self._last_error = str(pr.get("error") or "kalshi_positions_failed")
            return []
        raw = pr.get("positions_raw") or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            mp = raw.get("market_positions") or raw.get("positions") or raw.get("data") or []
            if isinstance(mp, list):
                for m in mp:
                    if not isinstance(m, dict):
                        continue
                    tid = m.get("ticker") or m.get("market_ticker")
                    pos = m.get("position") or m.get("count") or 0
                    try:
                        p = float(pos)
                    except (TypeError, ValueError):
                        p = 0.0
                    if tid and abs(p) > 1e-12:
                        rows.append({"trade_id": str(tid), "id": str(tid), "contracts": abs(p)})
        return normalize_external_open_positions(rows)

    def fetch_external_recent_fills(self) -> List[Dict[str, Any]]:
        return []

    def fetch_external_cash_state(self) -> Dict[str, Any]:
        from trading_ai.clients import kalshi as kalshi_client

        bal = kalshi_client.get_balance(self._settings)
        if not bal:
            return {"available": None, "currency": "USD", "source": "kalshi", "ok": False}
        return {"raw": bal, "source": "kalshi", "ok": True}

    def fetch_external_fees(self) -> Dict[str, Any]:
        return {"total": None, "source": "kalshi", "note": "fees_not_exposed_in_v1_adapter"}


def _state_path() -> Path:
    return runtime_root() / "state" / "venue_truth_state.json"


def _log_path() -> Path:
    return runtime_root() / "logs" / "venue_truth_sync_log.md"


def _default_state() -> Dict[str, Any]:
    return {"version": _STATE_VERSION, "last_sync": None, "history": []}


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
        out.setdefault("history", [])
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
        logger.warning("venue_truth log append failed: %s", exc)


def _compare_sets(
    internal_open_ids: List[str],
    ext_open: List[Dict[str, Any]],
    *,
    material_position_mismatch: int,
) -> tuple[str, int, List[str], List[str]]:
    ext_ids = {str(x.get("trade_id") or x.get("id") or "") for x in ext_open if x}
    int_ids = {str(x) for x in internal_open_ids if x}
    miss_int = int_ids - ext_ids
    miss_ext = ext_ids - int_ids
    delta = len(miss_int) + len(miss_ext)
    if delta == 0:
        return "ALIGNED", delta, sorted(miss_int), sorted(miss_ext)
    if delta <= material_position_mismatch:
        return "MINOR_DRIFT", delta, sorted(miss_int), sorted(miss_ext)
    return "MATERIAL_DRIFT", delta, sorted(miss_int), sorted(miss_ext)


def run_truth_sync(
    *,
    internal_open_ids: List[str],
    internal_cash: Optional[float],
    adapter: Optional[VenueTruthAdapter] = None,
    material_position_mismatch: int = 1,
    adapter_factory: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare internal snapshot vs adapter.

    ``adapter_factory``: ``mock`` | ``kalshi``. If ``adapter`` is provided, it wins.
    """
    ad: Optional[VenueTruthAdapter] = adapter
    detail: Optional[str] = None

    if ad is None:
        fac = (adapter_factory or "mock").strip().lower()
        if fac == "kalshi":
            try:
                from trading_ai.clients.kalshi import kalshi_signing_material_status
                from trading_ai.config import get_settings

                settings = get_settings()
                ks = kalshi_signing_material_status(settings)
                if not settings.kalshi_enabled:
                    row = _unsupported_row(
                        "kalshi_disabled_in_settings",
                        internal_open_ids,
                        internal_cash,
                        KalshiVenueTruthAdapter(settings),
                    )
                    _persist_and_hooks(row, material_hooks=False)
                    return row
                if not (ks.get("access_key_id") and ks.get("private_key")):
                    row = _unsupported_row(
                        "kalshi_credentials_unavailable",
                        internal_open_ids,
                        internal_cash,
                        KalshiVenueTruthAdapter(settings),
                    )
                    _persist_and_hooks(row, material_hooks=False)
                    return row
                ad = KalshiVenueTruthAdapter(settings)
            except Exception as exc:
                return _finalize_error(str(exc), internal_open_ids, internal_cash)
        else:
            ad = MockLocalVenueAdapter()

    assert ad is not None

    try:
        ext_open = ad.fetch_external_open_positions()
        for row in ext_open:
            ok, why = validate_external_position_row(row)
            if not ok:
                return _finalize_error(f"adapter_schema_violation:{why}", internal_open_ids, internal_cash)

        if isinstance(ad, KalshiVenueTruthAdapter) and getattr(ad, "_last_error", None):
            row = _unsupported_row(ad._last_error or "kalshi_fetch_failed", internal_open_ids, internal_cash, ad)
            _persist_and_hooks(row, material_hooks=False)
            return row

        verdict, delta, miss_int, miss_ext = _compare_sets(
            internal_open_ids, ext_open, material_position_mismatch=material_position_mismatch
        )
        cash_ext = ad.fetch_external_cash_state()
        fees_ext = ad.fetch_external_fees()

        row = _build_row(
            verdict,
            internal_open_ids,
            internal_cash,
            ad,
            ext_open,
            delta,
            miss_int,
            miss_ext,
            detail=detail,
            cash_external=cash_ext,
            fees_external=fees_ext,
        )
        _persist_and_hooks(row)
        return row
    except Exception as exc:
        logger.exception("run_truth_sync failed")
        return _finalize_error(str(exc), internal_open_ids, internal_cash, traceback.format_exc())


def _unsupported_row(reason: str, internal_open_ids: List[str], internal_cash: Optional[float], ad: VenueTruthAdapter) -> Dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": "UNSUPPORTED",
        "adapter_id": ad.adapter_id(),
        "detail": reason,
        "internal_open_count": len({str(x) for x in internal_open_ids if x}),
        "external_open_count": 0,
        "position_delta": 0,
        "missing_on_venue": [],
        "extra_on_venue": [],
        "cash_internal": internal_cash,
        "cash_external": {},
        "fees_external": {},
    }


def _build_row(
    verdict: str,
    internal_open_ids: List[str],
    internal_cash: Optional[float],
    ad: VenueTruthAdapter,
    ext_open: List[Dict[str, Any]],
    delta: int,
    miss_int: List[str],
    miss_ext: List[str],
    *,
    detail: Optional[str] = None,
    cash_external: Optional[Dict[str, Any]] = None,
    fees_external: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ce = cash_external if cash_external is not None else ad.fetch_external_cash_state()
    fe = fees_external if fees_external is not None else ad.fetch_external_fees()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "adapter_id": ad.adapter_id(),
        "detail": detail,
        "internal_open_count": len({str(x) for x in internal_open_ids if x}),
        "external_open_count": len(ext_open),
        "position_delta": delta,
        "missing_on_venue": miss_int,
        "extra_on_venue": miss_ext,
        "cash_internal": internal_cash,
        "cash_external": ce,
        "fees_external": fe,
    }


def _finalize_error(
    msg: str,
    internal_open_ids: List[str],
    internal_cash: Optional[float],
    tb: Optional[str] = None,
) -> Dict[str, Any]:
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": "ERROR",
        "adapter_id": "unknown",
        "detail": msg,
        "internal_open_count": len({str(x) for x in internal_open_ids if x}),
        "external_open_count": 0,
        "position_delta": 0,
        "missing_on_venue": [],
        "extra_on_venue": [],
        "cash_internal": internal_cash,
        "cash_external": {},
        "fees_external": {},
        "traceback": tb,
    }
    _persist_and_hooks(row, material_hooks=False)
    return row


def _persist_and_hooks(row: Dict[str, Any], *, material_hooks: bool = True) -> None:
    with _lock:
        st = _load()
        st["last_sync"] = row
        hist = list(st.get("history") or [])
        hist.append(row)
        st["history"] = hist[-128:]
        try:
            _save(st)
        except Exception as exc:
            logger.warning("venue_truth save failed: %s", exc)
    _append_log(row)
    if not material_hooks:
        return
    if row.get("verdict") == "MATERIAL_DRIFT":
        try:
            from trading_ai.ops.exception_dashboard import add_exception_event

            add_exception_event(
                category="truth_sync_drift",
                message="Venue truth sync MATERIAL_DRIFT",
                severity="HIGH",
                requires_review=True,
                extra={"row": row},
            )
        except Exception as exc:
            logger.warning("exception dashboard truth hook failed: %s", exc)
        try:
            from trading_ai.risk.hard_lockouts import update_lockout_state_from_execution_reconciliation

            update_lockout_state_from_execution_reconciliation(
                {
                    "trade_id": "truth_sync",
                    "execution_quality_verdict": "DISCREPANCY",
                    "requires_review": True,
                }
            )
        except Exception as exc:
            logger.warning("lockout hook truth sync failed: %s", exc)


def truth_sync_status() -> Dict[str, Any]:
    st = _load()
    last = st.get("last_sync") or {}
    return {
        "ok": True,
        "last_verdict": last.get("verdict"),
        "last_sync": last,
        "last_adapter_id": last.get("adapter_id"),
        "runtime_root": str(runtime_root()),
    }


def simulate_drift() -> Dict[str, Any]:
    ad = MockLocalVenueAdapter(
        {
            "open_positions": [{"trade_id": "ghost-1", "contracts": 1}],
            "recent_fills": [],
            "cash": {"available": 1000.0},
            "fees": {"total": 1.0},
        }
    )
    return run_truth_sync(internal_open_ids=["real-1"], internal_cash=1000.0, adapter=ad)


# Back-compat alias
VenueAdapter = VenueTruthAdapter
