#!/usr/bin/env python3
"""Smoke: first-60 live ops tick + research supervisor (proves OS wiring). Exits 0 on success."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))


def main() -> int:
    root = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or (_REPO / ".paper_smoke_runtime")).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ["EZRAS_LIVE_START_DATE"] = "2026-04-15"
    from trading_ai.control.first_60_day_ops import run_first_60_live_ops_tick
    from trading_ai.runtime.operating_system import run_role_supervisor_once

    for i in range(3):
        a = run_first_60_live_ops_tick(runtime_root=root, force=(i == 0))
        if not a.get("ok"):
            print("tick failed", a, file=sys.stderr)
            return 1
    hb = root / "data" / "control" / "first_60_live_ops_heartbeat.json"
    if not hb.is_file():
        print("missing heartbeat", hb, file=sys.stderr)
        return 2
    env_daily = root / "data" / "review" / "first_60_day_daily_envelope.json"
    if not env_daily.is_file():
        print("missing daily envelope", env_daily, file=sys.stderr)
        return 3
    rep = run_role_supervisor_once(role="research", runtime_root=root, skip_models=True, force_all_due=True)
    ran = list(rep.get("ran") or [])
    if "first_60_live_ops_tick" not in ran:
        print("supervisor did not run first_60_live_ops_tick", "ran=", ran, file=sys.stderr)
        return 4
    cal = root / "data" / "control" / "first_60_day_calendar.json"
    if not cal.is_file():
        print("missing calendar", cal, file=sys.stderr)
        return 5
    doc = json.loads(hb.read_text(encoding="utf-8"))
    if doc.get("truth_version") != "first_60_live_ops_heartbeat_v1":
        print("bad heartbeat truth_version", doc.get("truth_version"), file=sys.stderr)
        return 6
    print(json.dumps({"ok": True, "runtime_root": str(root), "supervisor_ran": ran}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
