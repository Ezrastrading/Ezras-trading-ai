"""On-disk layout for avenue / gate / worker hierarchy (governance intelligence only)."""

from __future__ import annotations

import os
from pathlib import Path


def default_bot_hierarchy_root() -> Path:
    raw = (os.environ.get("EZRAS_BOT_HIERARCHY_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "_governance_data" / "bot_hierarchy").resolve()


def ensure_hierarchy_dirs(root: Path | None = None) -> Path:
    r = root or default_bot_hierarchy_root()
    (r / "reports").mkdir(parents=True, exist_ok=True)
    (r / "knowledge").mkdir(parents=True, exist_ok=True)
    return r
