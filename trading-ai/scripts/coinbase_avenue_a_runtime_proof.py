#!/usr/bin/env python3
"""
Coinbase Avenue A — shadow/paper runtime proof (no live capital).

Usage:
  PYTHONPATH=src python3 scripts/coinbase_avenue_a_runtime_proof.py [--root DIR]

Writes RUNTIME_PROOF_REPORT.json and RUNTIME_PROOF_REPORT.md under the runtime root.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        type=str,
        default="",
        help="EZRAS_RUNTIME_ROOT (default: temp directory)",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(tempfile.mkdtemp(prefix="cb_avenue_a_proof_"))
    root.mkdir(parents=True, exist_ok=True)

    from trading_ai.runtime_proof.coinbase_shadow_paper_pass import run_full_proof

    os_mod = __import__("os")
    os_mod.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os_mod.environ.setdefault("ORG_ENGINE_MODE", "paper")

    report = run_full_proof(root)
    print("Runtime proof complete.")
    print("ROOT:", root)
    print("Report:", root / "RUNTIME_PROOF_REPORT.md")
    print("JSON:", root / "RUNTIME_PROOF_REPORT.json")
    print("trade_memory:", report["close_chain"]["artifact_paths"]["trade_memory"])
    print("scheduler:", report.get("scheduler"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
