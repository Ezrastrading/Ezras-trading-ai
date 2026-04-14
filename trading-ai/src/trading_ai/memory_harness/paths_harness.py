"""Runtime paths for memory harness integration points."""
from __future__ import annotations

import os
from pathlib import Path


def harness_data_dir() -> Path:
    root = os.environ.get("EZRAS_RUNTIME_ROOT", os.getcwd())
    p = Path(root) / ".memory_harness_data"
    p.mkdir(parents=True, exist_ok=True)
    return p
