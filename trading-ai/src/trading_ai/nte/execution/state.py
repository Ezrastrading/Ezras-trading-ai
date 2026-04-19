"""Open positions + engine state persistence."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_coinbase_positions_path

logger = logging.getLogger(__name__)


def default_state() -> Dict[str, Any]:
    return {
        "positions": [],
        "pending_entry_orders": [],
        "day_utc": None,
        "day_start_equity": None,
        "day_realized_pnl_usd": 0.0,
        "lifetime_realized_usd": 0.0,
        "consecutive_losses": 0,
        "paused_until": None,
    }


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or nte_coinbase_positions_path()
    if not p.exists():
        return default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default_state()
        for k, v in default_state().items():
            raw.setdefault(k, v)
        if not isinstance(raw.get("positions"), list):
            raw["positions"] = []
        if not isinstance(raw.get("pending_entry_orders"), list):
            raw["pending_entry_orders"] = []
        return raw
    except Exception as exc:
        logger.warning("NTE state load failed: %s", exc)
        return default_state()


def save_state(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or nte_coinbase_positions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def new_position_id() -> str:
    return uuid.uuid4().hex[:16]


def open_positions_list(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    pos = state.get("positions") or []
    return [p for p in pos if isinstance(p, dict)]
