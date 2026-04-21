from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple


def _repo_root_from_here() -> Path:
    # trading-ai/src/trading_ai/env/bootstrap.py -> trading-ai/
    return Path(__file__).resolve().parents[3]


def _parse_env_line(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    k, v = s.split("=", 1)
    key = k.strip()
    if not key:
        return None
    val = v.strip()
    # Strip simple surrounding quotes without trying to be a full dotenv parser.
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    # Support single-line PEM style: \n escapes
    if "\\n" in val:
        val = val.replace("\\n", "\n")
    return key, val


def load_env(*, env_file: Optional[Path] = None) -> Dict[str, str]:
    """
    Load `.env.runtime` into os.environ via setdefault (non-destructive).

    - Never prints secrets.
    - Never overrides explicitly set env vars.
    - Accepts single-line PEM values using `\\n` escapes.
    """
    root = _repo_root_from_here()
    p = Path(env_file) if env_file is not None else (root / ".env.runtime")
    applied: Dict[str, str] = {}
    try:
        if not p.is_file():
            return applied
        for line in p.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(line)
            if not parsed:
                continue
            key, val = parsed
            if key not in os.environ:
                os.environ[key] = val
                applied[key] = "<set>"
        return applied
    except OSError:
        return applied

