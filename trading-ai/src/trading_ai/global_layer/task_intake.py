"""
Task intake / dispatch (non-live).

Purpose:
- Convert append-only routed tasks.jsonl into durable per-bot "inbox" artifacts.
- Provide avenue/gate rollups so CEO/review loops have something machine-consumable.

This module never places orders. It only reads/writes runtime artifacts and governance JSONL.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trading_ai.global_layer._bot_paths import global_layer_governance_dir
from trading_ai.global_layer.bot_registry import load_registry


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _tasks_path() -> Path:
    return global_layer_governance_dir() / "tasks.jsonl"


def _task_intake_state_path(runtime_root: Path) -> Path:
    return runtime_root / "data" / "control" / "task_intake_state.json"


def _bot_inbox_dir(runtime_root: Path) -> Path:
    p = runtime_root / "data" / "control" / "bot_inboxes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bot_inbox_path(runtime_root: Path, bot_id: str) -> Path:
    return _bot_inbox_dir(runtime_root) / f"{bot_id}.json"


def _rollup_path(runtime_root: Path) -> Path:
    return runtime_root / "data" / "control" / "task_rollup.json"


@dataclass(frozen=True)
class IntakeConfig:
    max_new_tasks_per_tick: int = 50
    per_bot_inbox_limit: int = 80
    ignore_expired: bool = True


def _iter_jsonl_lines_tail(path: Path, *, start_line: int) -> Tuple[int, Iterable[Dict[str, Any]]]:
    """
    Best-effort JSONL streaming.

    We store the last consumed line number in state. This lets intake remain restart-safe even if
    tasks.jsonl grows forever (append-only audit stream).
    """
    if not path.is_file():
        return start_line, []
    lines = path.read_text(encoding="utf-8").splitlines()
    n_total = len(lines)
    if start_line < 0:
        start_line = 0
    if start_line >= n_total:
        return n_total, []

    def _rows() -> Iterable[Dict[str, Any]]:
        for ln in lines[start_line:]:
            ln = (ln or "").strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj

    return n_total, _rows()


def _is_expired(task: Dict[str, Any], *, now_iso: str) -> bool:
    exp = str(task.get("expires_at") or "").strip()
    if not exp:
        return False
    try:
        # ISO compare is safe when both are ISO UTC with same formatting.
        return exp < now_iso
    except Exception:
        return False


def _dedupe_keep_best(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    De-dupe by task_id keeping the highest priority and newest created_at.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        tid = str(t.get("task_id") or "").strip()
        if not tid:
            continue
        prev = best.get(tid)
        if prev is None:
            best[tid] = t
            continue
        try:
            p0 = int(prev.get("priority") or 0)
        except Exception:
            p0 = 0
        try:
            p1 = int(t.get("priority") or 0)
        except Exception:
            p1 = 0
        c0 = str(prev.get("created_at") or "")
        c1 = str(t.get("created_at") or "")
        if (p1, c1) > (p0, c0):
            best[tid] = t
    return list(best.values())


def _sort_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _key(t: Dict[str, Any]) -> Tuple[int, str]:
        try:
            pr = int(t.get("priority") or 0)
        except Exception:
            pr = 0
        return (pr, str(t.get("created_at") or ""))

    return sorted(tasks, key=_key, reverse=True)


def _scopes_rollup(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_avenue: Dict[str, int] = {}
    by_gate: Dict[str, int] = {}
    by_pair: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    for t in tasks:
        av = str(t.get("avenue") or "unknown")
        gate = str(t.get("gate") or "none")
        tt = str(t.get("task_type") or "unknown")
        by_avenue[av] = by_avenue.get(av, 0) + 1
        by_gate[gate] = by_gate.get(gate, 0) + 1
        by_pair[f"{av}|{gate}"] = by_pair.get(f"{av}|{gate}", 0) + 1
        by_type[tt] = by_type.get(tt, 0) + 1
    return {
        "by_avenue": dict(sorted(by_avenue.items(), key=lambda x: x[1], reverse=True)),
        "by_gate": dict(sorted(by_gate.items(), key=lambda x: x[1], reverse=True)),
        "by_avenue_gate": dict(sorted(by_pair.items(), key=lambda x: x[1], reverse=True)),
        "by_task_type": dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True)),
    }


def run_task_intake_once(*, runtime_root: Path, config: Optional[IntakeConfig] = None) -> Dict[str, Any]:
    """
    Read newly appended tasks from governance tasks.jsonl and update per-bot inbox artifacts.

    Writes:
    - data/control/task_intake_state.json
    - data/control/bot_inboxes/<bot_id>.json
    - data/control/task_rollup.json
    """
    cfg = config or IntakeConfig()
    root = Path(runtime_root).resolve()
    tasks_path = _tasks_path()
    now_iso = _iso()

    state_p = _task_intake_state_path(root)
    state = _read_json(state_p)
    start_line = int(state.get("last_consumed_line") or 0) if isinstance(state, dict) else 0

    total_lines, rows_iter = _iter_jsonl_lines_tail(tasks_path, start_line=start_line)
    new_rows: List[Dict[str, Any]] = []
    for r in rows_iter:
        if len(new_rows) >= int(cfg.max_new_tasks_per_tick):
            break
        if cfg.ignore_expired and _is_expired(r, now_iso=now_iso):
            continue
        # Only consider tasks that are assigned to a bot id (including unassigned_* buckets).
        ab = str(r.get("assigned_bot_id") or "").strip()
        if not ab:
            continue
        new_rows.append(r)

    new_rows = _sort_tasks(_dedupe_keep_best(new_rows))

    # Load known bots so we can produce per-role and per-scope rollups even when assigned_bot_id is a placeholder.
    reg = load_registry()
    known_bots = {str(b.get("bot_id") or "") for b in (reg.get("bots") or []) if isinstance(b, dict)}

    # Update inbox for each assigned bot bucket.
    inbox_updates: Dict[str, int] = {}
    for t in new_rows:
        bot_id = str(t.get("assigned_bot_id") or "").strip()
        if not bot_id:
            continue
        p = _bot_inbox_path(root, bot_id)
        cur = _read_json(p)
        xs = cur.get("tasks") if isinstance(cur.get("tasks"), list) else []
        xs = [x for x in xs if isinstance(x, dict)]
        xs.append(t)
        xs = _sort_tasks(_dedupe_keep_best(xs))[: max(1, int(cfg.per_bot_inbox_limit))]
        payload = {
            "truth_version": "bot_inbox_v1",
            "generated_at": now_iso,
            "bot_id": bot_id,
            "known_bot_id": bool(bot_id in known_bots),
            "task_count": len(xs),
            "tasks": xs,
        }
        _write_json_atomic(p, payload)
        inbox_updates[bot_id] = len(xs)

    # Rollup (across the new tasks only; steady-state rollup belongs to higher-level review logic).
    rollup = {
        "truth_version": "task_rollup_v1",
        "generated_at": now_iso,
        "runtime_root": str(root),
        "tasks_path": str(tasks_path),
        "intake": {
            "start_line": start_line,
            "end_line": total_lines,
            "new_tasks_seen": len(new_rows),
            "inboxes_updated": inbox_updates,
        },
        "rollup": _scopes_rollup(new_rows),
        "honesty": "Rollup is for newly ingested tasks only; tasks.jsonl remains append-only audit stream.",
    }
    _write_json_atomic(_rollup_path(root), rollup)

    # Persist state: even if we short-circuited due to max_new_tasks_per_tick, we still move the cursor
    # to the current file length to avoid reprocessing; tasks are durable in tasks.jsonl anyway.
    state_out = {
        "truth_version": "task_intake_state_v1",
        "generated_at": now_iso,
        "tasks_path": str(tasks_path),
        "last_consumed_line": int(total_lines),
        "last_tick_unix": time.time(),
        "last_new_tasks_seen": len(new_rows),
    }
    _write_json_atomic(state_p, state_out)
    return {"ok": True, "state_path": str(state_p), "rollup_path": str(_rollup_path(root)), "new": len(new_rows)}

