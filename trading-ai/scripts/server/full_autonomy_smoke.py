#!/usr/bin/env python3
"""
Smoke: N simulation ticks + supervisors; verifies durable artifacts and non-live gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def _need(p: Path) -> None:
    if not p.is_file():
        raise SystemExit(f"missing_artifact:{p}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=16)
    ap.add_argument("--runtime-root", default=None)
    args = ap.parse_args()

    root = Path(args.runtime_root).resolve() if args.runtime_root else Path(tempfile.mkdtemp(prefix="ezras_full_auto_")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    from trading_ai.runtime.operating_system import enforce_non_live_env_defaults, run_role_supervisor_once
    from trading_ai.simulation.engine import run_simulation_tick
    from trading_ai.simulation.nonlive import assert_nonlive_for_simulation

    enforce_non_live_env_defaults()
    assert_nonlive_for_simulation()

    for _ in range(max(1, int(args.ticks))):
        run_simulation_tick(runtime_root=root)
        run_role_supervisor_once(role="ops", runtime_root=root, skip_models=True, force_all_due=True)
        run_role_supervisor_once(role="research", runtime_root=root, skip_models=True, force_all_due=True)

    ctrl = root / "data" / "control"
    for name in (
        "sim_24h_summary.json",
        "sim_trade_log.json",
        "sim_fill_log.json",
        "sim_pnl.json",
        "sim_lessons.json",
        "sim_comparisons.json",
        "sim_tasks.json",
        "regression_drift.json",
    ):
        _need(ctrl / name)
    _need(ctrl / "operating_system" / "loop_status_ops.json")
    _need(ctrl / "operating_system" / "loop_status_research.json")
    # at least one loop artifact per role
    ops_loops = (ctrl / "operating_system" / "loops" / "ops").glob("*.json")
    rs_loops = (ctrl / "operating_system" / "loops" / "research").glob("*.json")
    if not list(ops_loops):
        raise SystemExit("missing_ops_loop_artifacts")
    if not list(rs_loops):
        raise SystemExit("missing_research_loop_artifacts")

    print(json.dumps({"ok": True, "runtime_root": str(root), "ticks": int(args.ticks)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
