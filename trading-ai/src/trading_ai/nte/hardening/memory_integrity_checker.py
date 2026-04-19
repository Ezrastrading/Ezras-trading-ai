"""Corruption detection and schema hints for NTE memory JSON."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.nte.hardening.failure_guard import FailureClass, log_failure
from trading_ai.nte.memory.store import MemoryStore

logger = logging.getLogger(__name__)

EXPECTED_SCHEMA_VERSION = 1


def check_memory_file(path: Path, name: str) -> Tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False, "not_object"
        sv = data.get("schema_version")
        if sv is not None and int(sv) != EXPECTED_SCHEMA_VERSION:
            return True, f"schema_mismatch:{sv}"
        return True, "ok"
    except Exception as exc:
        log_failure(
            FailureClass.MEMORY_CORRUPT,
            f"{name}: {exc}",
            severity="critical",
            pause_recommended=True,
            metadata={"path": str(path)},
        )
        return False, str(exc)


def run_integrity_scan(store: MemoryStore | None = None) -> List[Dict[str, Any]]:
    store = store or MemoryStore()
    store.ensure_defaults()
    results: List[Dict[str, Any]] = []
    for fname in MemoryStore.FILES:
        ok, reason = check_memory_file(store.path(fname), fname)
        results.append({"file": fname, "ok": ok, "reason": reason})
    return results
