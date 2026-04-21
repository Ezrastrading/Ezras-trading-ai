from __future__ import annotations

import os


def avenue_b_enabled() -> bool:
    """
    Avenue B isolation switch.
    Default: disabled.
    """
    return (os.environ.get("ENABLE_AVENUE_B") or "").strip().lower() in ("1", "true", "yes")

