#!/usr/bin/env python3
"""
Final organism lock verification — governance proof, execution dry-run, infra/recap JSON.

Does not place live orders. Does not print secrets.

Usage:
  PYTHONPATH=src python3 scripts/organism_final_lock_verify.py --runtime-root /path/to/EZRAS_RUNTIME_ROOT
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-root", type=str, required=True)
    args = ap.parse_args()
    root = Path(args.runtime_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "src"))

    os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(root))

    from trading_ai.runtime_proof.governance_live_enforcement_verify import verify_and_write_artifact
    from trading_ai.runtime_proof.nte_execution_chain_dry_run import run_dry_run_probes

    gov = verify_and_write_artifact(root)
    dry_path = run_dry_run_probes(root, n=5)

    infra = {
        "schema": "full_infra_sync_report_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "supabase": {
            "note": "Runtime verification requires valid SUPABASE_URL + key in environment when this script is run.",
            "client_resolution": "SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY via resolve_supabase_jwt_key",
            "railway": "not_verified_from_repo_ci_align_env_manually",
        },
        "dotenv": {
            "python_dotenv_recommended": True,
            "shell_source_unsafe_for_raw_multiline_pem": True,
        },
    }
    (root / "infra_proof").mkdir(parents=True, exist_ok=True)
    (root / "infra_proof" / "full_infra_sync_report.json").write_text(json.dumps(infra, indent=2), encoding="utf-8")

    recap = {
        "schema": "full_system_recap_v1",
        "systems_documented": [
            "local databank + NTE memory under EZRAS_RUNTIME_ROOT",
            "governance_order_gate with explicit env (no cache on enforcement flag)",
            "Supabase sync via supabase_trade_sync + shared JWT resolution",
            "NTE entry gates: governance first then live_routing_permitted",
        ],
        "artifacts_written_this_run": {
            "governance_live_enforcement_verified": str(root / "governance_proof" / "governance_live_enforcement_verified.json"),
            "execution_chain_validation": str(dry_path),
            "infra_sync": str(root / "infra_proof" / "full_infra_sync_report.json"),
        },
        "deterministic_vs_variable": {
            "deterministic": "governance decisions from joint snapshot + env; gate ordering in _nte_entry_gates_coinbase",
            "variable": "market data, strategy scores, latency, joint review content",
        },
        "git_status": _git_status(),
    }
    (root / "organism_proof").mkdir(parents=True, exist_ok=True)
    (root / "organism_proof" / "full_system_recap.json").write_text(json.dumps(recap, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "governance_test_results": gov.get("test_results"), "dry_run_jsonl": str(dry_path)}, indent=2))
    return 0 if gov.get("test_results") == "pass" else 1


def _git_status() -> dict:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"repo": str(REPO), "is_git": True, "porcelain_lines": len([x for x in r.stdout.splitlines() if x.strip()])}
    except Exception as e:
        return {"repo": str(REPO), "is_git": False, "error": str(e)}


if __name__ == "__main__":
    raise SystemExit(main())
