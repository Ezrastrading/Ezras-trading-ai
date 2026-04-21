#!/usr/bin/env python3
"""
Wall-clock style 24h-style simulation runner (still non-venue): many ticks, durable artifacts.

Uses ``--hours`` and ``--trades-per-hour`` to scale tick count; extends with a strict trade
target (>=100 closed sim rows) when needed for ``sim_24h_validation.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--trades-per-hour", type=int, default=6)
    ap.add_argument("--sleep-ms", type=int, default=0)
    ap.add_argument("--runtime-root", default=None)
    ap.add_argument("--min-closed-trades", type=int, default=100, help="Keep ticking until this many sim closes (cap 4000 extra)")
    args = ap.parse_args()

    root = Path(args.runtime_root).resolve() if args.runtime_root else Path(tempfile.mkdtemp(prefix="ezras_sim24_")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.runtime.operating_system import enforce_non_live_env_defaults, run_role_supervisor_once
    from trading_ai.simulation.engine import run_simulation_tick
    from trading_ai.simulation.nonlive import assert_nonlive_for_simulation
    from trading_ai.simulation.validation import write_sim_24h_validation

    enforce_non_live_env_defaults()
    assert_nonlive_for_simulation()

    total_ticks = max(1, int(args.hours) * max(1, int(args.trades_per_hour)))
    supervisor_rounds = 0
    for i in range(total_ticks):
        run_simulation_tick(runtime_root=root)
        if i % 5 == 0:
            run_role_supervisor_once(role="ops", runtime_root=root, skip_models=True, force_all_due=True)
            run_role_supervisor_once(role="research", runtime_root=root, skip_models=True, force_all_due=True)
            supervisor_rounds += 2
        if int(args.sleep_ms) > 0:
            time.sleep(int(args.sleep_ms) / 1000.0)

    ctrl = root / "data" / "control"
    need = max(0, int(args.min_closed_trades))
    extra = 0
    while extra < 4000:
        doc = {}
        if (ctrl / "sim_trade_log.json").is_file():
            try:
                doc = json.loads((ctrl / "sim_trade_log.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                doc = {}
        cnt = int(doc.get("count") or 0)
        if cnt >= need:
            break
        run_simulation_tick(runtime_root=root)
        if extra % 5 == 0:
            run_role_supervisor_once(role="ops", runtime_root=root, skip_models=True, force_all_due=True)
            run_role_supervisor_once(role="research", runtime_root=root, skip_models=True, force_all_due=True)
            supervisor_rounds += 2
        extra += 1

    val = write_sim_24h_validation(
        runtime_root=root,
        min_simulated_trades=need,
        min_supervisor_cycles=8,
        ticks_executed=total_ticks + extra,
    )
    summary = json.loads((ctrl / "sim_24h_summary.json").read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "ticks": total_ticks + extra, "summary": summary, "validation": val}, indent=2, default=str))
    return 0 if val.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
