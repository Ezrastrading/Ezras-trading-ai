#!/usr/bin/env python3
"""Enable FULL_AUTONOMY_ACTIVE (live-capable autonomy). Operator-only; does not bypass live_order_guard."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_src_dir() -> Path:
    return (Path(__file__).resolve().parents[2] / "src").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable FULL_AUTONOMY_ACTIVE (writes artifacts + live env).")
    parser.add_argument("--runtime-root", default="/opt/ezra-runtime", help="EZRAS_RUNTIME_ROOT (default: /opt/ezra-runtime)")
    parser.add_argument("--reason", default="operator_enable_full_autonomy_active_live")
    parser.add_argument(
        "--artifacts-only",
        action="store_true",
        help="Write mode/status JSON only; do not mutate process environment (for CI or staged rollout).",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(_repo_src_dir()))
    root = Path(args.runtime_root).expanduser().resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    from trading_ai.control.full_autonomy_mode import write_full_autonomy_active_live_artifacts

    out = write_full_autonomy_active_live_artifacts(
        runtime_root=root,
        reason=str(args.reason),
        apply_env=not bool(args.artifacts_only),
    )
    print("enabled FULL_AUTONOMY_ACTIVE")
    print(out.get("status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
