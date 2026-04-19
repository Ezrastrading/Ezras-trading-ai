#!/usr/bin/env python3
"""
Coinbase Avenue A — first-20 shadow/paper verification session (simulation harness).

Usage:
  PYTHONPATH=src python3 scripts/coinbase_first_twenty_session.py --root /path/to/runtime --trades 20
  PYTHONPATH=src python3 scripts/coinbase_first_twenty_session.py --preflight-only --root /path

Does not enable real capital. Set FIRST_TWENTY_ALLOW_LIVE=1 only under explicit supervised testing
(non-default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default="", help="EZRAS_RUNTIME_ROOT")
    ap.add_argument("--trades", type=int, default=20, help="Simulated completed trades (max 20)")
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--judge-archive", type=str, default="", help="Run artifact judge on this archive dir only")
    args = ap.parse_args()

    if args.judge_archive:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session, write_judge_report

        p = Path(args.judge_archive).resolve()
        j = judge_first_twenty_session(p)
        write_judge_report(p)
        print(json.dumps(j, indent=2))
        return 0

    root = Path(args.root).resolve() if args.root else Path(tempfile.mkdtemp(prefix="ft20_"))
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(root))
    os.environ.setdefault("TRADE_DATABANK_MEMORY_ROOT", str((root / "databank").resolve()))
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_PAPER_MODE", "true")
    os.environ.setdefault("ORG_ENGINE_MODE", "paper")

    from trading_ai.runtime_proof.first_twenty_session import (
        FirstTwentySessionConfig,
        run_first_twenty_shadow_session,
        run_preflight,
    )

    cfg = FirstTwentySessionConfig(runtime_root=root, max_completed_trades=min(20, max(1, args.trades)))

    if args.preflight_only:
        manifest, checklist = run_preflight(cfg)
        print(json.dumps({"manifest": manifest, "checklist": [{"name": a, "ok": o, "detail": d} for a, o, d in checklist]}, indent=2))
        crit = {"runtime_root_writable", "artifact_archive_ready", "coinbase_paper_shadow_safe"}
        return 0 if all(o for name, o, _ in checklist if name in crit) else 1

    out = run_first_twenty_shadow_session(cfg, simulate_trades=min(20, args.trades))
    print(json.dumps({k: out[k] for k in ("status", "recommendation", "rollback_reason") if k in out}, indent=2))
    arch = (out.get("manifest") or {}).get("artifact_archive") or ""
    print("archive:", arch)
    print("report:", root / "first_20_session_report.json")
    return 0 if out.get("status") != "aborted_preflight" and out.get("recommendation") == "PASS_SHADOW_VERIFICATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
