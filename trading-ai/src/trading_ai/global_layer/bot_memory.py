"""Per-bot memory files under ``nte/memory/bots/{bot_id}/`` (performance, trades, lessons)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.nte.paths import nte_memory_dir


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bot_memory_root(bot_id: str) -> Path:
    p = nte_memory_dir() / "bots" / str(bot_id).strip()
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_bot_memory_files(bot_id: str) -> Dict[str, Path]:
    root = bot_memory_root(bot_id)
    perf = root / "performance.json"
    trades = root / "trades.json"
    lessons = root / "lessons.json"
    if not perf.is_file():
        perf.write_text(
            json.dumps({"truth_version": "bot_performance_memory_v1", "updated_at": _iso(), "metrics": {}}, indent=2) + "\n",
            encoding="utf-8",
        )
    if not trades.is_file():
        trades.write_text(
            json.dumps({"truth_version": "bot_trades_memory_v1", "updated_at": _iso(), "entries": []}, indent=2) + "\n",
            encoding="utf-8",
        )
    if not lessons.is_file():
        lessons.write_text(
            json.dumps({"truth_version": "bot_lessons_memory_v1", "updated_at": _iso(), "lessons": []}, indent=2) + "\n",
            encoding="utf-8",
        )
    return {"performance": perf, "trades": trades, "lessons": lessons}


def ensure_orchestration_bot_memory_files(bot_id: str) -> Dict[str, Path]:
    """Extended canonical files for orchestration (versioned JSON, deterministic defaults)."""
    root = bot_memory_root(bot_id)
    out: Dict[str, Path] = {}
    specs = {
        "progress.json": "bot_progress_v1",
        "review.json": "bot_review_v1",
        "score.json": "bot_score_v1",
        "memory.json": "bot_memory_extended_v1",
        "replay_results.json": "bot_replay_results_v1",
        "research_notes.json": "bot_research_notes_v1",
    }
    for fname, ver in specs.items():
        p = root / fname
        out[fname] = p
        if not p.is_file():
            p.write_text(
                json.dumps({"truth_version": ver, "updated_at": _iso(), "entries": []}, indent=2) + "\n",
                encoding="utf-8",
            )
    return out


def read_performance(bot_id: str) -> Dict[str, Any]:
    paths = ensure_bot_memory_files(bot_id)
    return json.loads(paths["performance"].read_text(encoding="utf-8"))


def write_performance_merge(bot_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    paths = ensure_bot_memory_files(bot_id)
    cur = json.loads(paths["performance"].read_text(encoding="utf-8"))
    m = dict(cur.get("metrics") or {})
    m.update(patch)
    cur["metrics"] = m
    cur["updated_at"] = _iso()
    paths["performance"].write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cur


def append_trade_record(bot_id: str, record: Dict[str, Any]) -> None:
    paths = ensure_bot_memory_files(bot_id)
    cur = json.loads(paths["trades"].read_text(encoding="utf-8"))
    ent: List[Dict[str, Any]] = list(cur.get("entries") or [])
    ent.append({**record, "recorded_at": _iso()})
    cur["entries"] = ent
    cur["updated_at"] = _iso()
    paths["trades"].write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_lesson(bot_id: str, lesson: Dict[str, Any], *, approved_for_shared: bool = False) -> None:
    paths = ensure_bot_memory_files(bot_id)
    cur = json.loads(paths["lessons"].read_text(encoding="utf-8"))
    les: List[Dict[str, Any]] = list(cur.get("lessons") or [])
    les.append({**lesson, "recorded_at": _iso(), "approved_for_shared": bool(approved_for_shared)})
    cur["lessons"] = les
    cur["updated_at"] = _iso()
    paths["lessons"].write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
