#!/usr/bin/env python3
"""
Final pre-live blocker closure — Coinbase Avenue A (no live capital).

Usage:
  PYTHONPATH=src python3 scripts/coinbase_avenue_a_blocker_closure_pass.py [--root DIR]

Writes BLOCKER_CLOSURE_REPORT.json (+ existing RUNTIME_PROOF_REPORT.* from inner proof).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default="", help="Runtime root (default: temp dir)")
    ap.add_argument("--stress-ticks", type=int, default=600, help="Accelerated tick_scheduler iterations")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(tempfile.mkdtemp(prefix="cb_blocker_closure_"))
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from trading_ai.runtime_proof.coinbase_avenue_a_blocker_closure import run_blocker_closure_bundle

    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ.setdefault("ORG_ENGINE_MODE", "paper")

    report = run_blocker_closure_bundle(root, scheduler_stress_ticks=max(50, int(args.stress_ticks)))
    rubric = report.get("rubric") or {}
    print("BLOCKER_CLOSURE_REPORT:", root / "BLOCKER_CLOSURE_REPORT.json")
    print("RUBRIC:", rubric)
    return 0 if rubric.get("overall") == "GO_CONTROLLED_FIRST_20_CONSIDERATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
