#!/usr/bin/env python3
"""
Master smoke (now-live autonomy proof surface): runtime supervisor + simulation + tasks + live lock.

Legacy Day-A / Supabase-only checks removed; proof is artifact-backed under EZRAS_RUNTIME_ROOT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def _lessons_section_status(lessons: dict) -> tuple[str, bool, bool]:
    """Human-readable lessons subsection for legacy tests (structure vs Day-A completeness)."""
    if not isinstance(lessons, dict) or not lessons:
        return "❌ FAIL", False, False
    has_lessons = bool(lessons.get("lessons"))
    has_rules = isinstance(lessons.get("rules"), list)
    has_dnr = "do_not_repeat" in lessons
    day_a_complete = bool(lessons.get("day_a_complete"))
    healthy = has_lessons and has_rules and has_dnr
    if not healthy:
        return "❌ FAIL", False, day_a_complete
    if not day_a_complete:
        return "⚠️ WARN", True, False
    return "✅ OK", True, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-root", default=None)
    ap.add_argument("--cycles", type=int, default=14)
    args = ap.parse_args()

    from trading_ai.runtime.master_smoke import default_runtime_root, run_master_smoke

    root = Path(args.runtime_root).resolve() if args.runtime_root else default_runtime_root()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    out = run_master_smoke(runtime_root=root, cycles=int(args.cycles))
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
