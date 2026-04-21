from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


@dataclass(frozen=True)
class SnapshotWriteResult:
    ok: bool
    path: str
    error: str = ""


def _runtime_root(runtime_root: Optional[Path]) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _append_jsonl(runtime_root: Path, rel_path: str, row: Dict[str, Any]) -> SnapshotWriteResult:
    try:
        ad = LocalStorageAdapter(runtime_root=runtime_root)
        ad.ensure_parent(rel_path)
        p = ad.root() / rel_path
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return SnapshotWriteResult(ok=True, path=rel_path)
    except Exception as exc:
        return SnapshotWriteResult(ok=False, path=rel_path, error=f"{type(exc).__name__}:{exc}")


def write_master_snapshot(runtime_root: Optional[Path], row: Dict[str, Any]) -> SnapshotWriteResult:
    r = _runtime_root(runtime_root)
    row2 = {**row, "ts_written": time.time(), "snapshot_kind": "master"}
    return _append_jsonl(r, "data/trades/trades_master.jsonl", row2)


def write_edge_snapshot(runtime_root: Optional[Path], row: Dict[str, Any]) -> SnapshotWriteResult:
    r = _runtime_root(runtime_root)
    row2 = {**row, "ts_written": time.time(), "snapshot_kind": "edge"}
    return _append_jsonl(r, "data/trades/trades_edge_snapshot.jsonl", row2)


def write_execution_snapshot(runtime_root: Optional[Path], row: Dict[str, Any]) -> SnapshotWriteResult:
    r = _runtime_root(runtime_root)
    row2 = {**row, "ts_written": time.time(), "snapshot_kind": "execution"}
    return _append_jsonl(r, "data/trades/trades_execution_snapshot.jsonl", row2)


def write_review_snapshot(runtime_root: Optional[Path], row: Dict[str, Any]) -> SnapshotWriteResult:
    r = _runtime_root(runtime_root)
    row2 = {**row, "ts_written": time.time(), "snapshot_kind": "review"}
    return _append_jsonl(r, "data/trades/trades_review_snapshot.jsonl", row2)

