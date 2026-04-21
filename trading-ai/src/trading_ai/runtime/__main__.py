from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from trading_ai.runtime.operating_system import (
    enforce_non_live_env_defaults,
    release_role_lock,
    run_role_supervisor_once,
    tick_ops_once,
    tick_research_once,
    try_acquire_role_lock,
)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _print_json(obj: Dict[str, Any]) -> None:
    import json

    print(json.dumps(obj, indent=2, sort_keys=True))


def main() -> int:
    _setup_logging()
    p = argparse.ArgumentParser(prog="python -m trading_ai.runtime", description="Autonomous non-live operating system")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tick", help="Run one role tick (non-live)")
    t.add_argument("--role", required=True, choices=["ops", "research"])
    t.add_argument("--runtime-root", default=None, help="Optional EZRAS_RUNTIME_ROOT override")
    t.add_argument("--skip-models", action="store_true", help="Research tick: force stubbed reviews (default true)")

    d = sub.add_parser("daemon", help="Run role daemon loop (non-live)")
    d.add_argument("--role", required=True, choices=["ops", "research"])
    d.add_argument("--runtime-root", default=None)
    d.add_argument("--interval-sec", type=float, default=60.0, help="Sleep between ticks (default 60)")
    d.add_argument("--holder-id", default=None, help="Lock holder id (default pid-based)")
    d.add_argument("--skip-models", action="store_true", help="Research daemon: force stubbed reviews (default true)")
    d.add_argument("--cycles", type=int, default=0, help="Stop after N cycles (0=forever)")
    d.add_argument("--force-all-due", action="store_true", help="Supervisor: run all loops each cycle (for tests/smoke)")

    args = p.parse_args()
    enforce_non_live_env_defaults()

    rt = Path(args.runtime_root).resolve() if args.runtime_root else None

    if args.cmd == "tick":
        if args.role == "ops":
            _print_json(tick_ops_once(runtime_root=rt))
            return 0
        _print_json(tick_research_once(runtime_root=rt, skip_models=True if args.skip_models else True))
        return 0

    holder = args.holder_id or f"pid_{os.getpid()}"
    ok, why, lock = try_acquire_role_lock(role=args.role, holder_id=holder, runtime_root=rt, ttl_seconds=max(30.0, args.interval_sec * 3))
    if not ok:
        _print_json({"ok": False, "blocked": True, "reason": why})
        return 2
    try:
        n = 0
        while True:
            out = run_role_supervisor_once(
                role=args.role,
                runtime_root=rt,
                skip_models=True if args.skip_models else True,
                force_all_due=bool(args.force_all_due),
            )
            _print_json(out)
            n += 1
            if int(args.cycles or 0) > 0 and n >= int(args.cycles):
                return 0
            time.sleep(max(0.1, float(args.interval_sec)))
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            release_role_lock(role=args.role, holder_id=holder, runtime_root=rt)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

