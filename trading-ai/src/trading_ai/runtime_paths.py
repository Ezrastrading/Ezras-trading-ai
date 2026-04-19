"""Single canonical ``EZRAS_RUNTIME_ROOT`` resolution for the whole organism.

Precedence:
1. ``EZRAS_RUNTIME_ROOT`` if set (after strip/expanduser).
2. ``/app/ezras-runtime`` when ``/app`` exists (Railway-style).
3. Otherwise ``~/ezras-runtime``.

This matches :func:`trading_ai.shark.required_env.require_ezras_runtime_root` defaults.
Do not duplicate this logic elsewhere — import from here or call ``require_ezras_runtime_root()`` at process entry
to populate the environment for subprocesses.
"""

from __future__ import annotations

import os
from pathlib import Path


def ezras_runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if os.path.exists("/app"):
        return Path("/app/ezras-runtime").resolve()
    return (Path.home() / "ezras-runtime").resolve()
