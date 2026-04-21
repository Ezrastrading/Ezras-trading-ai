#!/usr/bin/env python3
"""
Wall-clock style 24h-style simulation runner (still non-venue): many ticks, durable artifacts.

Uses ``--hours`` and ``--trades-per-hour`` to scale tick count; ``--sleep-ms`` throttles between ticks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--trades-per-hour", type=int, default=6)
    ap.add_argument("--sleep-ms", type=int, default=0)
    ap.add_argument("--runtime-root", default=None)
    args = ap.parse_args()

    root = Path(args.runtime_root).resolve() if args.runtime_root else Path(tempfile.mkdtemp(prefix="ezras_sim24_")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.runtime.operating_system import enforce_non_live_env_defaults
    from trading_ai.simulation.engine import run_simulation_tick
    from trading_ai.simulation.nonlive import assert_nonlive_for_simulation

    enforce_non_live_env_defaults()
    assert_nonlive_for_simulation()

    total_ticks = max(1, int(args.hours) * max(1, int(args.trades_per_hour)))
    for i in range(total_ticks):
        run_simulation_tick(runtime_root=root)
        if int(args.sleep_ms) > 0:
            time.sleep(int(args.sleep_ms) / 1000.0)

    ctrl = root / "data" / "control"
    summary = json.loads((ctrl / "sim_24h_summary.json").read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "ticks": total_ticks, "summary": summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
