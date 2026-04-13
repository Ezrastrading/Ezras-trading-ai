#!/usr/bin/env python3
"""Run from repo root: python scripts/validate_env.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_ai.validate_env import run_validation  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_validation())
