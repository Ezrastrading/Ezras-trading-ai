"""Append-first JSONL + atomic JSON aggregates for Trade Intelligence Databank."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from trading_ai.governance.storage_architecture import avenue_memory_dir
from trading_ai.nte.utils.atomic_json import atomic_write_json

logger = logging.getLogger(__name__)

_ENV_ROOT = "TRADE_DATABANK_MEMORY_ROOT"
_ENV_EZRAS = "EZRAS_RUNTIME_ROOT"


class DatabankRootUnsetError(RuntimeError):
    """Neither ``TRADE_DATABANK_MEMORY_ROOT`` nor ``EZRAS_RUNTIME_ROOT`` is set."""


def resolve_databank_root() -> Tuple[Path, str]:
    """
    Resolve Trade Intelligence databank root — **no silent global fallback**.

    Precedence:
    1. ``TRADE_DATABANK_MEMORY_ROOT`` — explicit databank directory.
    2. ``EZRAS_RUNTIME_ROOT`` / ``databank`` — session-scoped default (tests, runtime proof, operators).

    Raises :class:`DatabankRootUnsetError` if neither is set (prevents bleed into shared home/runtime paths).
    """
    override = (os.environ.get(_ENV_ROOT) or "").strip()
    if override:
        p = Path(override).expanduser().resolve()
        return p, "TRADE_DATABANK_MEMORY_ROOT"
    ez = (os.environ.get(_ENV_EZRAS) or "").strip()
    if ez:
        p = (Path(ez).expanduser().resolve() / "databank")
        return p, "EZRAS_RUNTIME_ROOT/databank"
    raise DatabankRootUnsetError(
        f"Set {_ENV_ROOT} or {_ENV_EZRAS} so the Trade Intelligence databank path is explicit "
        f"(recommended: {_ENV_EZRAS}/databank when using a session runtime root)."
    )


def databank_memory_root() -> Path:
    p, _src = resolve_databank_root()
    p.mkdir(parents=True, exist_ok=True)
    return p


def global_trade_events_path() -> Path:
    return databank_memory_root() / "trade_events.jsonl"


def global_trade_scores_path() -> Path:
    return databank_memory_root() / "trade_scores.json"


def path_daily_summary() -> Path:
    return databank_memory_root() / "daily_trade_summary.json"


def path_weekly_summary() -> Path:
    return databank_memory_root() / "weekly_trade_summary.json"


def path_monthly_summary() -> Path:
    return databank_memory_root() / "monthly_trade_summary.json"


def path_strategy_performance() -> Path:
    return databank_memory_root() / "strategy_performance_summary.json"


def path_avenue_performance() -> Path:
    return databank_memory_root() / "avenue_performance_summary.json"


def path_write_verification() -> Path:
    return databank_memory_root() / "trade_write_verification.json"


def path_databank_health() -> Path:
    return databank_memory_root() / "trade_databank_health.json"


def avenue_trade_events_path(avenue_name: str) -> Path:
    return avenue_memory_dir(avenue_name) / "trade_events.jsonl"


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else dict(default)
    except Exception as exc:
        logger.warning("local_trade_store read %s: %s — reset", path, exc)
        return dict(default)


def load_jsonl_trade_ids(path: Path) -> Set[str]:
    ids: Set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                tid = rec.get("trade_id")
                if isinstance(tid, str):
                    ids.add(tid)
            except json.JSONDecodeError:
                continue
    return ids


def append_jsonl_atomic(path: Path, record: Dict[str, Any], *, trade_id: str) -> bool:
    """Append one JSON line; return False if trade_id already present (dedupe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_jsonl_trade_ids(path)
    if trade_id in existing:
        logger.info("append_jsonl_atomic: duplicate trade_id %s — skip append", trade_id)
        return False
    line = json.dumps(record, default=str) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return True


def load_all_trade_events(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or global_trade_events_path()
    out: List[Dict[str, Any]] = []
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    out.append(rec)
            except json.JSONDecodeError:
                continue
    return out


def load_trade_scores() -> Dict[str, Any]:
    default = {"by_trade_id": {}, "updated": None}
    return _read_json(global_trade_scores_path(), default)


def save_trade_scores(data: Dict[str, Any]) -> None:
    from datetime import datetime, timezone

    data = dict(data)
    data["updated"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(global_trade_scores_path(), data)


def upsert_score_record(
    trade_id: str,
    event: Dict[str, Any],
    scores: Dict[str, Any],
    *,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    data = load_trade_scores()
    by = data.get("by_trade_id")
    if not isinstance(by, dict):
        by = {}
    rec: Dict[str, Any] = {
        "trade_id": trade_id,
        "scores": scores,
        "event_ref": {k: event.get(k) for k in ("avenue_id", "asset", "strategy_id", "timestamp_close")},
    }
    if extra_meta:
        rec["meta"] = extra_meta
    by[trade_id] = rec
    data["by_trade_id"] = by
    save_trade_scores(data)


def save_aggregate(path: Path, data: Dict[str, Any]) -> None:
    atomic_write_json(path, data)


def load_aggregate(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    return _read_json(path, default)


def ensure_seed_files() -> None:
    """Create empty aggregate JSON shells if missing."""
    root = databank_memory_root()
    root.mkdir(parents=True, exist_ok=True)
    seeds = [
        (global_trade_scores_path(), {"by_trade_id": {}, "updated": None}),
        (path_daily_summary(), {"rollups": [], "by_day": {}, "updated": None}),
        (path_weekly_summary(), {"rollups": [], "by_week": {}, "updated": None}),
        (path_monthly_summary(), {"rollups": [], "by_month": {}, "updated": None}),
        (path_strategy_performance(), {"rows": [], "updated": None}),
        (path_avenue_performance(), {"rows": [], "updated": None}),
        (path_write_verification(), {"entries": [], "last": None, "updated": None}),
        (path_databank_health(), {"status": "ok", "issues": [], "updated": None}),
    ]
    for path, default in seeds:
        if not path.exists():
            atomic_write_json(path, default)
