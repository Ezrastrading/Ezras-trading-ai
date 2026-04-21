#!/usr/bin/env python3
"""
Focused proof: two-server autonomous non-live operating system.

Runs:
- ops tick (non-live)
- research tick (non-live, optional stubbed reviews)
- ops + research supervisors (2 cycles, forced all loops)
- role lock collision proof

Never places live orders.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _pp(x: object) -> None:
    print(json.dumps(x, indent=2, sort_keys=True))


def main() -> None:
    root = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or "").resolve()
    if not root:
        root = Path(tempfile.mkdtemp(prefix="ezras_os_smoke_")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    from trading_ai.runtime.operating_system import (
        enforce_non_live_env_defaults,
        tick_ops_once,
        tick_research_once,
        try_acquire_role_lock,
    )

    enforce_non_live_env_defaults()

    print("=== AUTONOMOUS NON-LIVE OS SMOKE ===")
    print(f"runtime_root={root}")
    print()

    print("1) OPS TICK")
    ops = tick_ops_once(runtime_root=root)
    _pp(ops)
    print()

    print("2) RESEARCH TICK (stubbed reviews if --skip-models)")
    skip = (os.environ.get("EZRAS_OS_SMOKE_SKIP_MODELS") or "1").strip().lower() in ("1", "true", "yes")
    res = tick_research_once(runtime_root=root, skip_models=skip)
    _pp(res)
    print()

    print("2b) OPS SUPERVISOR PROOF (2 cycles, forced all loops)")
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    _pp(run_role_supervisor_once(role="ops", runtime_root=root, force_all_due=True))
    _pp(run_role_supervisor_once(role="ops", runtime_root=root, force_all_due=True))
    print()

    print("2c) RESEARCH SUPERVISOR PROOF (2 cycles, forced all loops)")
    _pp(run_role_supervisor_once(role="research", runtime_root=root, skip_models=skip, force_all_due=True))
    _pp(run_role_supervisor_once(role="research", runtime_root=root, skip_models=skip, force_all_due=True))
    print()

    print("3) ROLE LOCK PROOF (same role collision prevented)")
    ok1, why1, _ = try_acquire_role_lock(role="ops", holder_id="smoke_holder_1", runtime_root=root, ttl_seconds=30)
    ok2, why2, _ = try_acquire_role_lock(role="ops", holder_id="smoke_holder_2", runtime_root=root, ttl_seconds=30)
    _pp({"first_ok": ok1, "first_reason": why1, "second_ok": ok2, "second_reason": why2})
    print()

    print("=== SMOKE COMPLETE ===")
    print("Safety: live execution env defaults enforced (paper/dry_run).")


if __name__ == "__main__":
    main()
