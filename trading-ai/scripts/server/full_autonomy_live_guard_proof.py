#!/usr/bin/env python3
"""
Proof: simulation entry refuses live-trading env flags (blocked attempt is visible).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ezras_live_guard_")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ["NTE_EXECUTION_MODE"] = "live"
    os.environ["NTE_LIVE_TRADING_ENABLED"] = "true"
    os.environ["COINBASE_EXECUTION_ENABLED"] = "true"

    from trading_ai.simulation.nonlive import LiveTradingNotAllowedError, assert_nonlive_for_simulation

    blocked = False
    detail = ""
    try:
        assert_nonlive_for_simulation()
    except LiveTradingNotAllowedError as exc:
        blocked = True
        detail = str(exc)

    out = {
        "ok": True,
        "blocked_attempt": blocked,
        "detail": detail,
        "honesty": "Live flags were set intentionally to prove simulation refuses to run.",
    }
    print(json.dumps(out, indent=2))
    return 0 if blocked else 2


if __name__ == "__main__":
    sys.exit(main())
