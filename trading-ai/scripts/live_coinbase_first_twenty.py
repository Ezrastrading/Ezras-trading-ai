#!/usr/bin/env python3
"""
Coinbase Avenue A — controlled live first-20 **operator** entry (preflight + manifest).

Phase 0 preflight must pass before any live capital. This script does **not** submit orders.

Preflight only (recommended):
  PYTHONPATH=src python3 scripts/live_coinbase_first_twenty.py --preflight \\
    --runtime-root /path/to/EZRAS_RUNTIME_ROOT \\
    --simulated-judge /path/to/first_20_judge_report.json

Print required env (no checks):
  PYTHONPATH=src python3 scripts/live_coinbase_first_twenty.py --print-requirements
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve()
    repo = here.parents[1]
    sys.path.insert(0, str(repo / "src"))

    ap = argparse.ArgumentParser(description="Live first-20 operator preflight (exit 0 = all critical checks pass).")
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="Explicit marker: run Phase 0 live preflight only (this script never places orders).",
    )
    ap.add_argument(
        "--print-requirements",
        action="store_true",
        help="Print required environment / files and exit 0 (no preflight).",
    )
    ap.add_argument("--runtime-root", type=str, default="", help="EZRAS_RUNTIME_ROOT (required for preflight)")
    ap.add_argument(
        "--simulated-judge",
        type=str,
        default="",
        help="first_20_judge_report.json from simulated run (or set LIVE_FIRST_20_SIMULATED_JUDGE_JSON)",
    )
    args = ap.parse_args()

    from trading_ai.runtime_proof.live_first_20_operator import (
        LIVE_FIRST_20_PREFLIGHT_ENV_REFERENCE,
        run_live_preflight,
        write_live_manifest,
        write_live_session_md,
        write_live_session_report,
    )

    if args.print_requirements:
        print(LIVE_FIRST_20_PREFLIGHT_ENV_REFERENCE)
        return 0

    root = Path(args.runtime_root).resolve() if args.runtime_root else repo / "runtime_proof_runs" / "live_operator_default"
    judge = Path(args.simulated_judge).resolve() if args.simulated_judge else None

    ok, manifest, checks = run_live_preflight(
        runtime_root=root,
        trading_ai_repo_root=repo,
        simulated_judge_path=judge,
    )
    manifest["cli_explicit_preflight_flag"] = bool(args.preflight)
    arch = manifest.get("artifact_archive")
    ad = Path(arch) if arch else root
    mp = write_live_manifest(ad, manifest)
    (root / "live_first_20_session_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    write_live_session_report(
        ad,
        {
            "session_id": manifest["session_id"],
            "status": "preflight_pass" if ok else "aborted_preflight",
            "preflight_pass": ok,
            "checks": checks,
        },
    )
    write_live_session_md(
        ad,
        {
            "session_id": manifest["session_id"],
            "status": "preflight_pass" if ok else "aborted_preflight",
            "completed_trades": 0,
            "rollback_reason": None,
            "final_result": "ABORT" if not ok else "PREFLIGHT_OK_NO_TRADES_RUN",
            "detail": {"checks": checks},
        },
    )
    out = {
        "preflight_ok": ok,
        "exit_code": 0 if ok else 1,
        "manifest_path": str(mp),
        "archive": str(ad),
        "runtime_root": str(root),
        "explicit_preflight_cli": args.preflight,
    }
    print(json.dumps(out, indent=2))

    print("\n--- checks ---", file=sys.stderr)
    for c in checks:
        mark = "PASS" if c["pass"] else "FAIL"
        print(f"[{c['id']}] {mark} {c['name']}: {c['detail']}", file=sys.stderr)
        if not c["pass"] and c.get("remediation"):
            print(f"    remediation: {c['remediation']}", file=sys.stderr)

    if not ok:
        print("\n--- FAILED: fix items above; see --print-requirements ---", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
