#!/usr/bin/env python3
"""Best-effort systemd status for Ezra units → ``data/control/service_status.json``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-root", default=None)
    ap.add_argument("--units", nargs="*", default=["ezra-ops.service", "ezra-research.service"])
    args = ap.parse_args()

    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(args.runtime_root).resolve() if args.runtime_root else ezras_runtime_root()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)

    units: dict[str, dict[str, str]] = {}
    for u in args.units:
        row: dict[str, str] = {}
        for cmd, key in (("is-enabled", "is_enabled"), ("is-active", "is_active")):
            try:
                cp = subprocess.run(
                    ["systemctl", cmd, u],
                    capture_output=True,
                    text=True,
                    timeout=6,
                    check=False,
                )
                row[key] = (cp.stdout or "").strip() or (cp.stderr or "").strip() or f"exit_{cp.returncode}"
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                row[key] = f"unavailable:{type(exc).__name__}"
        units[u] = row

    doc = {
        "truth_version": "service_status_v1",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "units": units,
        "honesty": "Requires systemctl on Linux; macOS/dev returns unavailable without error.",
    }
    p = ctrl / "service_status.json"
    p.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(doc, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
