"""Merged JSON memory + rolling 48h mastery snapshot (read-first, non-destructive)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _parse_ts(line: Dict[str, Any]) -> Optional[datetime]:
    s = str(line.get("timestamp") or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _tail_jsonl_since(path: Path, since: datetime) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines[-5000:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        ts = _parse_ts(o)
        if ts is None or ts < since:
            continue
        out.append(o)
    return out


def load_self_learning_memory(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(runtime_root or ezras_runtime_root()).resolve() / "data" / "learning" / "self_learning_memory.json"
    if not p.is_file():
        return {
            "version": 1,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "recent_event_counts": {},
            "last_event_types": [],
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_self_learning_memory(mem: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "learning" / "self_learning_memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {**mem, "updated_at_utc": datetime.now(timezone.utc).isoformat()}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def touch_memory_after_event(event_type: str, *, runtime_root: Optional[Path] = None) -> None:
    mem = load_self_learning_memory(runtime_root=runtime_root)
    counts = mem.get("recent_event_counts")
    if not isinstance(counts, dict):
        counts = {}
    counts[event_type] = int(counts.get(event_type) or 0) + 1
    mem["recent_event_counts"] = counts
    le = mem.get("last_event_types")
    if not isinstance(le, list):
        le = []
    le.insert(0, {"ts": datetime.now(timezone.utc).isoformat(), "event_type": event_type})
    mem["last_event_types"] = le[:200]
    save_self_learning_memory(mem, runtime_root=runtime_root)


def refresh_last_48h_mastery(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    log = root / "data" / "learning" / "system_learning_log.jsonl"
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    rows = _tail_jsonl_since(log, since)
    by_type: Dict[str, int] = {}
    for r in rows:
        et = str(r.get("event_type") or "unknown")
        by_type[et] = by_type.get(et, 0) + 1
    ceo = sum(1 for r in rows if r.get("requires_ceo_review"))
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_hours": 48,
        "entries_in_window": len(rows),
        "by_event_type": by_type,
        "requires_ceo_review_count": ceo,
        "honest_note": "Counts are from local append-only log only; not synced externally.",
    }
    jp = root / "data" / "learning" / "last_48h_system_mastery.json"
    tp = root / "data" / "learning" / "last_48h_system_mastery.txt"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tp.write_text(
        "\n".join(
            [
                "LAST 48H SYSTEM MASTERY (learning log rollup)",
                f"entries: {len(rows)}",
                f"ceo_review_flags: {ceo}",
                json.dumps(by_type, indent=2),
            ]
        ),
        encoding="utf-8",
    )
    return payload


def write_system_mastery_report(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    m48 = refresh_last_48h_mastery(runtime_root=root)
    mem = load_self_learning_memory(runtime_root=root)
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "last_48h": m48,
        "memory_headline": {
            "recent_event_counts": mem.get("recent_event_counts"),
        },
        "classification": "advisory_operator_readable_not_execution_enforced",
    }
    p1 = root / "data" / "learning" / "system_mastery_report.json"
    p2 = root / "data" / "learning" / "system_mastery_report.txt"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    p2.write_text(json.dumps(out, indent=2, default=str)[:12000] + "\n", encoding="utf-8")
    return out
