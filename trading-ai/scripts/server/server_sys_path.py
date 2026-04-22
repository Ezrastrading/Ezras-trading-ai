"""Ensure dual-repo ``src`` trees are on ``sys.path`` (private before public).

CLI scripts are often invoked without systemd; services set ``PYTHONPATH`` but a
bare ``python /opt/.../script.py`` does not. Import probes must match production.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_dual_repo_src_on_path(*, public_root: Path, private_root: Path) -> tuple[str, str]:
    pub = str((public_root / "trading-ai" / "src").resolve())
    pri = str((private_root / "trading-ai" / "src").resolve())
    os.environ["PYTHONPATH"] = f"{pri}:{pub}"
    for p in (pub, pri):
        while p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, pub)
    sys.path.insert(0, pri)
    return pri, pub
