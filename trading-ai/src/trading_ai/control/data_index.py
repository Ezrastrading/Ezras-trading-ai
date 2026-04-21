"""Index of control/report artifacts under ``EZRAS_RUNTIME_ROOT`` for CEO / ratio / validation readers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _mtime_iso(p: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def refresh_data_index(
    *,
    runtime_root: Optional[Path] = None,
    rel_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Walk ``data/control`` and ``data/reports`` (and optional extra relative roots) for ``*.json`` / ``*.txt``.
    Writes ``data/control/data_index.json``.
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    roots = rel_roots or [
        "data/control",
        "data/reports",
        "data/deployment",
        "data/learning",
        "data/review",
    ]
    artifacts: List[Dict[str, Any]] = []
    for rr in roots:
        base = root / rr
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".json", ".txt", ".jsonl", ".csv"):
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            artifacts.append(
                {
                    "path": rel,
                    "suffix": p.suffix.lower(),
                    "mtime_utc": _mtime_iso(p),
                    "size_bytes": p.stat().st_size,
                }
            )

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "artifact_count": len(artifacts),
        "artifacts": sorted(artifacts, key=lambda x: x.get("path") or ""),
    }
    out_path = root / "data" / "control" / "data_index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    return out
