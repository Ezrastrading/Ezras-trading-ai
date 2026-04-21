from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter


P_TRACKER_STATE = "data/asymmetric/asymmetric_tracker_state.json"
P_EVENTS = "data/asymmetric/asymmetric_trade_events.jsonl"


@dataclass(frozen=True)
class AsymmetricTrackerState:
    truth_version: str
    total_trades: int
    wins: int
    losses: int
    net_pnl_total: float
    ev_total: float
    capital_deployed_total: float
    capital_returned_total: float
    max_win: float
    updated_at_unix: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "truth_version": self.truth_version,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "net_pnl_total": self.net_pnl_total,
            "ev_total": self.ev_total,
            "capital_deployed_total": self.capital_deployed_total,
            "capital_returned_total": self.capital_returned_total,
            "max_win": self.max_win,
            "updated_at_unix": self.updated_at_unix,
        }


def default_tracker_state() -> Dict[str, Any]:
    return AsymmetricTrackerState(
        truth_version="asymmetric_tracker_v1",
        total_trades=0,
        wins=0,
        losses=0,
        net_pnl_total=0.0,
        ev_total=0.0,
        capital_deployed_total=0.0,
        capital_returned_total=0.0,
        max_win=0.0,
        updated_at_unix=time.time(),
    ).to_dict()


def _ad(runtime_root: Optional[Any]) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=runtime_root)


def load_tracker_state(*, runtime_root: Optional[Any] = None) -> Dict[str, Any]:
    ad = _ad(runtime_root)
    j = ad.read_json(P_TRACKER_STATE)
    if isinstance(j, dict):
        return j
    return default_tracker_state()


def record_asymmetric_trade_event(
    event: Dict[str, Any],
    *,
    runtime_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Append-first event + atomic aggregate state.

    Event contract (best-effort, evidence-first):
    - trade_id, venue_id, gate_id, product_id/symbol
    - ev (expected value dollars, if known)
    - capital_deployed, capital_returned
    - net_pnl (realized)
    """
    ad = _ad(runtime_root)
    ad.ensure_parent(P_EVENTS)
    p = ad.root() / P_EVENTS
    p.parent.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row.setdefault("ts_unix", time.time())
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")

    st = load_tracker_state(runtime_root=runtime_root)
    st = dict(st)
    st["updated_at_unix"] = time.time()
    st["total_trades"] = int(st.get("total_trades") or 0) + 1

    net = row.get("net_pnl")
    try:
        net_f = float(net) if net is not None else None
    except (TypeError, ValueError):
        net_f = None
    if net_f is not None:
        st["net_pnl_total"] = float(st.get("net_pnl_total") or 0.0) + net_f
        if net_f > 0:
            st["wins"] = int(st.get("wins") or 0) + 1
            st["max_win"] = max(float(st.get("max_win") or 0.0), net_f)
        elif net_f < 0:
            st["losses"] = int(st.get("losses") or 0) + 1

    ev = row.get("ev")
    try:
        ev_f = float(ev) if ev is not None else None
    except (TypeError, ValueError):
        ev_f = None
    if ev_f is not None:
        st["ev_total"] = float(st.get("ev_total") or 0.0) + ev_f

    dep = row.get("capital_deployed")
    ret = row.get("capital_returned")
    try:
        dep_f = float(dep) if dep is not None else None
    except (TypeError, ValueError):
        dep_f = None
    try:
        ret_f = float(ret) if ret is not None else None
    except (TypeError, ValueError):
        ret_f = None
    if dep_f is not None:
        st["capital_deployed_total"] = float(st.get("capital_deployed_total") or 0.0) + dep_f
    if ret_f is not None:
        st["capital_returned_total"] = float(st.get("capital_returned_total") or 0.0) + ret_f

    ad.write_json(P_TRACKER_STATE, st)
    return st


def record_asymmetric_trade_from_normalized_record(
    record: Dict[str, Any],
    *,
    ev_usd: Optional[float] = None,
    runtime_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Bridge: update asymmetric tracker from a ``NormalizedTradeRecord``-shaped dict.

    This keeps asymmetric accounting isolated and lets execution wiring stay venue-specific.
    """
    r = dict(record or {})
    if str(r.get("trade_type") or "").strip().lower() != "asymmetric":
        return load_tracker_state(runtime_root=runtime_root)

    # Best-effort mapping (record fields are optional across venues).
    event: Dict[str, Any] = {
        "source": "normalized_trade_record",
        "trade_id": r.get("trade_id"),
        "avenue_id": r.get("avenue_id"),
        "venue_id": r.get("avenue_name") or r.get("venue_id"),
        "gate_id": r.get("gate_id"),
        "capital_bucket_id": r.get("capital_bucket_id"),
        "strategy_id": r.get("strategy_id"),
        "execution_profile": r.get("execution_profile"),
        "instrument_kind": r.get("instrument_kind"),
        "product_id": r.get("product_id"),
        "symbol": r.get("symbol"),
        "net_pnl": r.get("net_pnl"),
        "capital_deployed": r.get("quote_spent"),
        "capital_returned": r.get("proceeds_received"),
    }
    if ev_usd is not None:
        event["ev"] = float(ev_usd)
    return record_asymmetric_trade_event(event, runtime_root=runtime_root)

