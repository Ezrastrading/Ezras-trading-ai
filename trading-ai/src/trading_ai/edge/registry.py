"""Persistent EdgeRegistry — local JSON truth, optional Supabase mirror."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.edge.models import EdgeRecord, EdgeStatus
from trading_ai.edge.paths import edge_registry_path
from trading_ai.nte.utils.atomic_json import atomic_write_json

logger = logging.getLogger(__name__)

REGISTRY_SCHEMA_VERSION = "1.0.0"


def _default_root() -> Dict[str, Any]:
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "edges": []}


class EdgeRegistry:
    """Load/save edge definitions; idempotent upserts by edge_id."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or edge_registry_path()

    @property
    def path(self) -> Path:
        return self._path

    def load_raw(self) -> Dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.is_file():
            return _default_root()
        try:
            d = json.loads(self._path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else _default_root()
        except Exception as exc:
            logger.warning("edge registry read failed %s: %s — reset", self._path, exc)
            return _default_root()

    def save_raw(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, data)

    def list_edges(self) -> List[EdgeRecord]:
        raw = self.load_raw()
        out: List[EdgeRecord] = []
        for row in raw.get("edges") or []:
            if isinstance(row, dict) and row.get("edge_id"):
                try:
                    out.append(EdgeRecord.from_json(row))
                except Exception as exc:
                    logger.debug("skip bad edge row: %s", exc)
        return out

    def get(self, edge_id: str) -> Optional[EdgeRecord]:
        for e in self.list_edges():
            if e.edge_id == edge_id:
                return e
        return None

    def upsert(self, edge: EdgeRecord) -> None:
        data = self.load_raw()
        edges = [e for e in (data.get("edges") or []) if isinstance(e, dict)]
        found = False
        for i, row in enumerate(edges):
            if str(row.get("edge_id")) == edge.edge_id:
                edges[i] = edge.to_json()
                found = True
                break
        if not found:
            edges.append(edge.to_json())
        data["edges"] = edges
        data["schema_version"] = REGISTRY_SCHEMA_VERSION
        self.save_raw(data)
        try:
            from trading_ai.edge.supabase_edge_sync import mirror_edge_registry_row

            mirror_edge_registry_row(edge.to_json())
        except Exception as exc:
            logger.debug("supabase edge mirror: %s", exc)

    def update_status(
        self,
        edge_id: str,
        status: str,
        *,
        reason: str = "",
        extra_history: Optional[Dict[str, Any]] = None,
    ) -> bool:
        e = self.get(edge_id)
        if e is None:
            return False
        hist = {
            "at": e.updated_at,
            "from": e.status,
            "to": status,
            "reason": reason,
        }
        if extra_history:
            hist.update(extra_history)
        e.promotion_history.append(hist)
        e.status = status
        e.updated_at = datetime.now(timezone.utc).isoformat()
        if status == EdgeStatus.REJECTED.value and reason:
            e.rejection_reason = reason
        self.upsert(e)
        return True

    @staticmethod
    def new_edge_id() -> str:
        return f"edge_{uuid.uuid4().hex[:16]}"


def ensure_registry_file(path: Optional[Path] = None) -> Path:
    reg = EdgeRegistry(path)
    if not reg.path.is_file():
        reg.save_raw(_default_root())
    return reg.path
