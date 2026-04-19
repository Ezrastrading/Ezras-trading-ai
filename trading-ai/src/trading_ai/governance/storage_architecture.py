"""Storage under EZRAS_RUNTIME_ROOT/shark — auditable state + audit JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.runtime_paths import ezras_runtime_root


def runtime_root() -> Path:
    """Canonical organism runtime root (same as automation/risk_bucket)."""
    return ezras_runtime_root()


def shark_data_dir() -> Path:
    """Canonical: ~/ezras-runtime/shark (Section 11)."""
    return runtime_root() / "shark"


def shark_state_path(name: str) -> Path:
    d = shark_data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def shark_state_backups_dir() -> Path:
    p = shark_data_dir() / "state" / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def shark_audit_log_path() -> Path:
    p = shark_data_dir() / "logs" / "shark_audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def shark_sqlite_path() -> Path:
    return shark_state_path("shark.sqlite")


def global_memory_dir() -> Path:
    """Global organism memory (goals, speed, knowledge) — under ``shark/memory/global``."""
    p = shark_data_dir() / "memory" / "global"
    p.mkdir(parents=True, exist_ok=True)
    return p


def global_avenue_knowledge_dir() -> Path:
    p = global_memory_dir() / "avenue_knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def avenue_memory_dir(avenue_name: str) -> Path:
    """Per-avenue mirror under ``shark/memory/avenues/<name>`` (e.g. coinbase, kalshi)."""
    safe = avenue_name.strip().lower().replace("..", "").replace("/", "")
    p = shark_data_dir() / "memory" / "avenues" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_shark_audit_record(record: Dict[str, Any]) -> None:
    p = shark_audit_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
