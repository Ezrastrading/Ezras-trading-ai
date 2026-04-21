"""
Fail-closed lifecycle snapshots for reconstructable trade truth.

If/when a real DB exists, these JSONL streams can be replayed into it.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.storage.storage_adapter import LocalStorageAdapter

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshots_rel_dir() -> str:
    return "data/snapshots"


def _table_rel_path(table: str) -> str:
    t = str(table or "").strip()
    if not t:
        t = "unknown_table"
    return f"{_snapshots_rel_dir()}/{t}.jsonl"


@dataclass(frozen=True)
class SnapshotWriteResult:
    ok: bool
    table: str
    path: str
    error: Optional[str] = None


class SnapshotWriteError(RuntimeError):
    pass


def append_snapshot_row(
    *,
    table: str,
    row: Dict[str, Any],
    runtime_root: Optional[Path] = None,
    require_trade_id: bool = True,
) -> SnapshotWriteResult:
    """
    Append one JSONL snapshot row.

    Contract:
    - Writes are append-only.
    - If write fails, raises SnapshotWriteError (callers must mark trade invalid).
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    rel = _table_rel_path(table)
    try:
        ad.ensure_parent(rel)
        payload = dict(row or {})
        payload.setdefault("snapshot_ts", _iso_now())
        if require_trade_id and not str(payload.get("trade_id") or "").strip():
            payload["trade_id"] = f"ts_{uuid.uuid4().hex[:16]}"
        p = ad.root() / rel
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
        return SnapshotWriteResult(ok=True, table=str(table), path=str(p))
    except Exception as exc:
        msg = f"snapshot_write_failed table={table}: {type(exc).__name__}: {exc}"
        logger.error(msg)
        raise SnapshotWriteError(msg) from exc


def _finite_num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def mark_trade_invalid(trade: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    trade["truth_valid"] = False
    trade["truth_invalid_reason"] = str(reason or "snapshot_failed")
    trade.setdefault("truth_invalid_at", _iso_now())
    return trade


def enforce_snapshot_integrity(
    *,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    edge_ok: bool,
    execution_ok: bool,
    review_ok: bool,
) -> None:
    """
    Fail-closed integrity gate.

    If any required snapshot write failed, raise SnapshotWriteError.
    """
    if edge_ok and execution_ok and review_ok:
        return
    missing = []
    if not edge_ok:
        missing.append("edge_snapshot")
    if not execution_ok:
        missing.append("execution_snapshot")
    if not review_ok:
        missing.append("review_snapshot")
    raise SnapshotWriteError(
        f"snapshot_integrity_failed trade_id={trade_id} avenue_id={avenue_id} gate_id={gate_id} missing={','.join(missing)}"
    )


def snapshot_trades_master(
    *,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    payload: Dict[str, Any],
    runtime_root: Optional[Path] = None,
) -> SnapshotWriteResult:
    return append_snapshot_row(
        table="trades_master",
        row={
            "trade_id": str(trade_id),
            "avenue_id": str(avenue_id),
            "gate_id": str(gate_id),
            "truth_valid": bool(payload.get("truth_valid", True)),
            "truth_invalid_reason": payload.get("truth_invalid_reason"),
            "master": dict(payload),
        },
        runtime_root=runtime_root,
    )


def snapshot_trades_edge(
    *,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    edge_snapshot: Dict[str, Any],
    runtime_root: Optional[Path] = None,
) -> SnapshotWriteResult:
    return append_snapshot_row(
        table="trades_edge_snapshot",
        row={
            "trade_id": str(trade_id),
            "avenue_id": str(avenue_id),
            "gate_id": str(gate_id),
            "edge_at_entry": dict(edge_snapshot),
            "edge": edge_snapshot.get("edge_family") or edge_snapshot.get("edge_id"),
            "confidence": _finite_num(edge_snapshot.get("confidence") or edge_snapshot.get("edge_confidence")),
            "liquidity_score": _finite_num(edge_snapshot.get("liquidity_score")),
            "net_expected_edge_bps": _finite_num(
                edge_snapshot.get("net_expected_edge_bps")
                or edge_snapshot.get("net_edge_bps")
                or edge_snapshot.get("expected_net_edge_bps")
            ),
        },
        runtime_root=runtime_root,
    )


def snapshot_trades_execution(
    *,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    execution_snapshot: Dict[str, Any],
    runtime_root: Optional[Path] = None,
) -> SnapshotWriteResult:
    return append_snapshot_row(
        table="trades_execution_snapshot",
        row={
            "trade_id": str(trade_id),
            "avenue_id": str(avenue_id),
            "gate_id": str(gate_id),
            "execution": dict(execution_snapshot),
        },
        runtime_root=runtime_root,
    )


def snapshot_trades_review(
    *,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    review_snapshot: Dict[str, Any],
    runtime_root: Optional[Path] = None,
) -> SnapshotWriteResult:
    return append_snapshot_row(
        table="trades_review_snapshot",
        row={
            "trade_id": str(trade_id),
            "avenue_id": str(avenue_id),
            "gate_id": str(gate_id),
            "review": dict(review_snapshot),
            "execution_grade": review_snapshot.get("execution_grade")
            or review_snapshot.get("execution_score")
            or review_snapshot.get("trade_quality_score"),
        },
        runtime_root=runtime_root,
    )

