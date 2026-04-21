"""Fingerprint-based artifact refresh — no time-only refresh; avoid overwrite of authoritative proofs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

# Logical artifact name -> list of dependency paths (relative to runtime root) that influence it
DEPENDENCY_GRAPH: Dict[str, List[str]] = {
    "runtime_runner_truth": [
        "data/control/system_execution_lock.json",
        "data/control/system_kill_switch.json",
        "data/control/failsafe_status.json",
    ],
    "avenue_orchestration_truth": [
        "data/control/system_execution_lock.json",
        "data/control/go_no_go_decision.json",
        "data/control/execution_mirror_results.json",
    ],
    "execution_loop_truth": [
        "data/control/system_execution_lock.json",
    ],
}

_STORE_REL = "data/control/_artifact_refresh_state.json"


def _fingerprint_files(root: Path, rels: List[str]) -> str:
    h = hashlib.sha256()
    ad = LocalStorageAdapter(runtime_root=root)
    for rel in sorted(rels):
        p = ad.root() / rel
        if p.is_file():
            try:
                st = p.stat()
                h.update(rel.encode())
                h.update(str(st.st_mtime_ns).encode())
                h.update(str(st.st_size).encode())
            except OSError:
                h.update(rel.encode())
                h.update(b"missing")
        else:
            h.update(rel.encode())
            h.update(b"missing")
    return h.hexdigest()[:32]


def should_refresh(artifact: str, *, runtime_root: Path) -> bool:
    deps = DEPENDENCY_GRAPH.get(artifact)
    if not deps:
        return False
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    cur_fp = _fingerprint_files(ad.root(), deps)
    store = ad.read_json(_STORE_REL) or {}
    key = f"fp:{artifact}"
    prev = store.get(key)
    return prev != cur_fp


def mark_refreshed(artifact: str, *, runtime_root: Path) -> None:
    deps = DEPENDENCY_GRAPH.get(artifact) or []
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    store = ad.read_json(_STORE_REL) or {}
    store[f"fp:{artifact}"] = _fingerprint_files(ad.root(), deps)
    ad.write_json(_STORE_REL, store)


def refresh_if_stale(
    artifact: str,
    writer: Callable[[Path], Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Call writer only when dependencies changed; mark fingerprint after successful write."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    if not should_refresh(artifact, runtime_root=root):
        return {"artifact": artifact, "refreshed": False, "reason": "deps_unchanged"}
    try:
        writer(root)
        mark_refreshed(artifact, runtime_root=root)
        return {"artifact": artifact, "refreshed": True}
    except Exception as exc:
        return {"artifact": artifact, "refreshed": False, "error": str(exc)}
