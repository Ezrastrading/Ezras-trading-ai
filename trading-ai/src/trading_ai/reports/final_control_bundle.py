"""
Operator rerun pack + repo confidence audit (writes data/control/*).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trading_ai.runtime_paths import ezras_runtime_root


def _repo_root() -> Path:
    # .../trading-ai/src/trading_ai/reports/this_file.py -> repo is parents[3]
    return Path(__file__).resolve().parents[3]


# Curated high-signal suites (not the entire tests/ tree — avoids multi-hour runs).
CURATED_PYTEST_PATHS: Tuple[Tuple[str, Sequence[str]], ...] = (
    (
        "gate_b_staged_micro_parity",
        (
            "tests/test_gate_b_parity_smoke.py",
            "tests/test_gate_b_micro_validation_runner.py",
            "tests/test_gate_b_engine_system.py",
        ),
    ),
    (
        "gate_a_live_proof_deployment",
        (
            "tests/test_deployment_proof.py",
            "tests/test_live_micro_validation_product_cascade.py",
            "tests/test_live_micro_validation_run_record.py",
            "tests/test_live_execution_validation_fills.py",
            "tests/test_live_validation_poll_resolution.py",
            "tests/test_verify_data_pipeline_trade_events.py",
        ),
    ),
    (
        "routing_policy_readiness",
        (
            "tests/test_runtime_coinbase_policy_unified.py",
            "tests/test_routing_validation_coherent.py",
            "tests/test_final_wiring_readiness.py",
            "tests/test_deployment_operator_pack.py",
        ),
    ),
    (
        "safety_prelive_hardening",
        (
            "tests/test_duplicate_trade_window.py",
            "tests/test_failsafe_prelive_hardening.py",
            "tests/test_system_execution_lock.py",
            "tests/test_safety_truth_validation_lock.py",
            "tests/test_final_hardening_smoke.py",
        ),
    ),
    (
        "intelligence_learning_scoping",
        (
            "tests/test_intelligence_live_hooks.py",
            "tests/test_gap_closure_honest.py",
            "tests/test_scope_contamination.py",
            "tests/test_multi_avenue.py",
        ),
    ),
    (
        "coinbase_nte_spot",
        (
            "tests/test_coinbase_spot_fill_truth.py",
            "tests/test_nte_hardening_smoke.py",
            "tests/test_spot_routing_smoke.py",
        ),
    ),
)


def run_curated_pytest_and_capture() -> Dict[str, Any]:
    root = _repo_root()
    all_paths: List[str] = []
    for _, paths in CURATED_PYTEST_PATHS:
        all_paths.extend(paths)
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=line", *all_paths]
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    summary_line = lines[-1] if lines else ""
    if not summary_line and proc.returncode == 0:
        summary_line = "pytest completed (see stdout_tail)"
    return {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "summary_line": summary_line,
        "stdout_tail": (proc.stdout or "")[-8000:],
        "stderr_tail": (proc.stderr or "")[-4000:],
        "suites": [{"name": n, "files": list(p)} for n, p in CURATED_PYTEST_PATHS],
    }


def write_final_repo_confidence_audit(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    rt = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = rt / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    run = run_curated_pytest_and_capture()
    skipped = [
        "tests/integration/* (not in curated pass — run separately before production)",
        "tests/test_shark_system.py (large — run in dedicated session)",
        "Remaining tests/* not listed in CURATED_PYTEST_PATHS",
    ]
    ok = run["exit_code"] == 0
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(rt),
        "curated_pytest": run,
        "pass_fail_interpretation": "exit_code_0_means_all_listed_tests_passed",
        "skipped_groups": skipped,
        "confidence_summary": (
            "High confidence in listed areas if exit_code is 0. "
            "Full-repo confidence requires running remaining tests and integration suites separately."
            if ok
            else "Review stdout_tail / stderr_tail — at least one failure in curated suite."
        ),
    }
    (ctrl / "final_repo_confidence_audit.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "FINAL REPO CONFIDENCE AUDIT (curated pytest only)",
        f"Generated: {payload['generated_at']}",
        f"Pytest exit code: {run['exit_code']}",
        f"Summary: {run.get('summary_line') or 'see JSON stdout_tail'}",
        "",
        "Skipped (documented):",
        *[f"  - {s}" for s in skipped],
        "",
        payload["confidence_summary"],
    ]
    (ctrl / "final_repo_confidence_audit.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def write_final_rerun_operator_pack(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    rt = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = rt / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(rt),
        "gate_a_rerun_validation": {
            "commands": [
                "export EZRAS_RUNTIME_ROOT=/path/to/your/runtime",
                "python -m trading_ai.deployment validation-products",
                "python -m trading_ai.deployment checklist",
                "python -m trading_ai.deployment micro-validation --n 3 --product-id BTC-USD",
                "python -m trading_ai.deployment readiness",
                "python -m trading_ai.deployment final-report",
            ],
            "success_looks_like": {
                "checklist_exit_0_or_ready_for_live_micro_validation_true": "deployment checklist JSON under runtime",
                "micro_validation_exit_0": "live_validation_streak_passed true; live_validation_*.json under data/deployment/live_validation_runs/",
                "execution_proof": "execution_proof/live_execution_validation.json with FINAL_EXECUTION_PROVEN true after real round-trip",
            },
            "failure_looks_like": "non-zero exit codes; READY_FOR_FIRST_20 false in proof; governance / credential errors in logs",
            "artifacts_to_inspect": [
                "execution_proof/live_execution_validation.json",
                "data/deployment/live_validation_runs/live_validation_*.json",
                "data/deployment/live_validation_streak.json",
                "data/deployment/final_readiness.json",
                "data/deployment/final_readiness_report.txt",
                "data/ledger/trade_ledger.jsonl",
                "databank/trade_events.jsonl (if configured)",
            ],
        },
        "gate_b_staged_only": {
            "commands": [
                "export EZRAS_RUNTIME_ROOT=/path/to/your/runtime",
                'python -c "from trading_ai.prelive.gate_b_staged_validation import run; run(runtime_root=__import__(\"pathlib\").Path(\"$EZRAS_RUNTIME_ROOT\"))"',
            ],
            "success_looks_like": {
                "gate_b_validation_json": "data/control/gate_b_validation.json with micro_validation_pass true",
                "validation_mode": "staged_mock_no_venue_orders — not live venue proof",
            },
            "still_staged_only": "No authenticated Coinbase order IDs for Gate B are asserted by this path.",
        },
        "gate_b_live_micro_coinbase": {
            "commands": [
                "export EZRAS_RUNTIME_ROOT=/path/to/your/runtime",
                "export GATE_B_LIVE_EXECUTION_ENABLED=true",
                "export GATE_B_LIVE_MICRO_VALIDATION_CONFIRM=YES_I_UNDERSTAND_GATE_B_CAPITAL_ROUND_TRIP",
                "python -m trading_ai.deployment gate-b-live-micro --quote-usd 10 --product-id BTC-USD",
                'python -c "from trading_ai.reports.gate_parity_reports import write_final_system_lock_status; write_final_system_lock_status()"',
            ],
            "success_looks_like": {
                "execution_proof": "execution_proof/gate_b_live_execution_validation.json with FINAL_EXECUTION_PROVEN and gate_b_order_verified true",
                "control_merge": "data/control/gate_b_validation.json updated with live_venue_micro_validation_pass when round-trip completes",
            },
            "honesty": "Same real-capital seriousness as Gate A live validation; proof file is distinct from staged harness artifacts.",
        },
        "first_few_trades": {
            "must_be_true": [
                "Gate A execution_proof shows FINAL_EXECUTION_PROVEN and coinbase_order_verified",
                "deployment checklist + system_execution_lock allow gate_a",
                "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM=YES_I_UNDERSTAND_REAL_CAPITAL",
                "Coinbase + Supabase env configured as required",
            ],
            "still_blocks": "See data/control/live_enablement_truth.txt",
        },
    }
    (ctrl / "final_rerun_operator_pack.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    txt = f"""FINAL RERUN / VALIDATION OPERATOR PACK
Generated: {payload['generated_at']}
Runtime root (set EZRAS_RUNTIME_ROOT): {rt}

--- Gate A (real live micro) ---
1) validation-products:
   python -m trading_ai.deployment validation-products
2) checklist:
   python -m trading_ai.deployment checklist
3) micro-validation (REAL capital at risk if guards pass):
   python -m trading_ai.deployment micro-validation --n 3 --product-id BTC-USD
4) readiness + report:
   python -m trading_ai.deployment readiness
   python -m trading_ai.deployment final-report

Success: execution_proof/live_execution_validation.json exists; FINAL_EXECUTION_PROVEN true; streak file updated.
Failure: exit code non-zero; proof missing; READY_FOR_FIRST_20 false.

--- Gate B (staged / mock only) ---
Run staged validation (no venue orders):
  python -c "from pathlib import Path; from trading_ai.prelive.gate_b_staged_validation import run; run(runtime_root=Path('$EZRAS_RUNTIME_ROOT'))"
(Staged output is NOT live venue proof.)

--- Gate B (live micro — real Coinbase round-trip) ---
Requires GATE_B_LIVE_EXECUTION_ENABLED=true, system lock gate_b enabled, and confirm env.
  python -m trading_ai.deployment gate-b-live-micro --quote-usd 10 --product-id BTC-USD
Success: execution_proof/gate_b_live_execution_validation.json with FINAL_EXECUTION_PROVEN true (not satisfied by staged files).

--- First few trades ---
Read data/control/live_enablement_truth.txt for exact env blockers.

"""
    (ctrl / "final_rerun_operator_pack.txt").write_text(txt, encoding="utf-8")
    return payload
