"""Client order id de-duplication (TTL via file-backed ring)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from trading_ai.shark.production_hardening import paths as ph_paths


def _load_ids() -> List[Dict[str, Any]]:
    p = ph_paths.recent_order_ids_json()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("ids") or [])
    except Exception:
        return []


def _save_ids(ids: List[Dict[str, Any]]) -> None:
    p = ph_paths.recent_order_ids_json()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ids": ids[-500:], "updated": time.time()}, indent=2), encoding="utf-8")


def is_duplicate_client_order_id(cid: str) -> bool:
    return any(str(x.get("client_order_id")) == cid for x in _load_ids())


def register_client_order_id(cid: str, *, venue: str = "") -> None:
    ids = _load_ids()
    ids.append({"client_order_id": cid, "venue": venue, "ts": time.time()})
    _save_ids(ids)
