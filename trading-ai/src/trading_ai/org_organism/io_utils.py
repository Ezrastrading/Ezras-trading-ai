"""Small JSON / JSONL helpers for organism artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


def read_json_dict(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
