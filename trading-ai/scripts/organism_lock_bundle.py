#!/usr/bin/env python3
"""
Emit organism lock artifacts (isolation, stress snapshot, parity, Kalshi, goals, boundary).

Usage:
  PYTHONPATH=src python3 scripts/organism_lock_bundle.py --root /tmp/org_run

Does not enable live trading. Uses stub stress with modest iterations unless --stress-iter N.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, required=True, help="EZRAS_RUNTIME_ROOT (writable temp)")
    ap.add_argument("--stress-iter", type=int, default=64, help="Stress harness iterations (default 64 for speed)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(root / "databank")
    os.environ.setdefault("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    os.environ.setdefault("ORGANISM_REVIEW_STUB_ONLY", "1")

    from trading_ai.runtime_proof.avenue_parity_report import write_avenue_parity_report
    from trading_ai.runtime_proof.databank_isolation_report import write_databank_isolation_report
    from trading_ai.runtime_proof.environment_noise_report import write_environment_noise_report
    from trading_ai.runtime_proof.execution_boundary_report import write_boundary_artifacts
    from trading_ai.runtime_proof.goal_progress_reports import write_goal_progress_artifacts
    from trading_ai.runtime_proof.governance_ordering_report import write_governance_ordering_report
    from trading_ai.runtime_proof.kalshi_process_contract import write_kalshi_process_artifacts
    from trading_ai.runtime_proof.kalshi_readiness_report import write_kalshi_parity_status
    from trading_ai.runtime_proof.organism_stress_harness import run_organism_stress_harness, run_organism_soak_harness

    out: dict = {}
    out["governance_ordering"] = str(write_governance_ordering_report(root))
    out["databank_isolation"] = str(write_databank_isolation_report(root, expect_empty_databank=False))
    stress_out = run_organism_stress_harness(root, iterations=max(32, args.stress_iter), review_cycle_every=max(8, args.stress_iter // 4))
    out["stress"] = stress_out["output_dir"]
    soak_out = run_organism_soak_harness(root, test_mode=True)
    out["soak"] = soak_out["output_dir"]
    out["environment_noise"] = str(
        write_environment_noise_report(root, stress_used_skip_models=True, bundle_preflight=True)
    )
    out["avenue_parity"] = str(write_avenue_parity_report(root))
    out["kalshi"] = str(write_kalshi_parity_status(root))
    out["kalshi_process"] = {k: str(v) for k, v in write_kalshi_process_artifacts(root).items()}
    out["goals"] = {k: str(v) for k, v in write_goal_progress_artifacts(root).items()}
    out["boundary"] = {k: str(v) for k, v in write_boundary_artifacts(root).items()}

    summary_path = root / "organism_lock_bundle_summary.json"
    summary_path.write_text(json.dumps({"ok": True, "artifacts": out}, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "summary": str(summary_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
