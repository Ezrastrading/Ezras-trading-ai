"""Operator runbook: runtime proof sequence (GO / NO-GO)."""

from __future__ import annotations

from trading_ai.deployment.paths import deployment_data_dir, runtime_proof_runbook_path


def write_runtime_proof_runbook(*, write_file: bool = True) -> str:
    """
    Writes ``data/deployment/runtime_proof_runbook.txt`` with exact commands and order.
    """
    text = """EZRAS TRADING AI — RUNTIME PROOF RUNBOOK (GO / NO-GO)
======================================================

First-20 live trading is NEVER auto-started by these commands. Enable first-20 only after
STEP 6 shows READY and your human sign-off.

GO means: all gates passed — deployment checklist green, three live micro-validation round
trips passed with full proof booleans, final readiness reports ``ready_for_first_20: true``,
no trading halt file, and governance / Supabase / reconciliation / ops outputs proofs green.

NO-GO means: any critical blocker remains — do not enable first-20; fix the failing step and
re-run from the step indicated in ``final_readiness_report.txt`` (checklist vs micro-validation
vs governance vs Supabase vs reconciliation vs ops outputs).

Prerequisites: working directory is the ``trading-ai`` package root (the folder that contains
``src/``). All CLI examples assume: ``cd`` into that directory first.

STEP 1 — Apply Supabase migrations in documented order
   Open ``supabase/MIGRATION_ORDER.txt`` in the repo and apply SQL files to your Supabase
   project in that order (remote schema must match what the app expects).

STEP 2 — Confirm deploy / runtime parity
   Parity is evaluated as part of STEP 3 (checklist). Artifact when run:
   ``data/deployment/deployment_parity_report.json``
   Supabase schema inventory + remote checks:
   ``data/deployment/supabase_schema_readiness.json``

STEP 3 — Run deployment checklist
   cd trading-ai && PYTHONPATH=src python3 -m trading_ai.deployment checklist
   Artifacts: ``data/deployment/deployment_checklist.json``, ``deployment_checklist.txt``
   Pass: ``ready_for_live_micro_validation`` = true in JSON.

STEP 4 — Run three real micro-validations (smallest live notional; no scale-up; no first-20)
   PYTHONPATH=src python3 -m trading_ai.deployment micro-validation --n 3
   Artifacts:
   ``data/deployment/live_validation_runs/live_validation_001.json``
   ``data/deployment/live_validation_runs/live_validation_002.json``
   ``data/deployment/live_validation_runs/live_validation_003.json``
   ``data/deployment/live_validation_streak.json``
   Pass: ``live_validation_streak_passed`` = true in streak JSON.
   Optional env (smallest size; default 5 USD if venue allows): ``LIVE_MICRO_VALIDATION_QUOTE_USD``,
   ``DEPLOYMENT_VALIDATION_QUOTE_USD`` — actual chosen notional is recorded per run file.

STEP 5 — Run final readiness
   PYTHONPATH=src python3 -m trading_ai.deployment readiness
   Artifact: ``data/deployment/final_readiness.json``

STEP 6 — Read final report for GO / NO-GO
   PYTHONPATH=src python3 -m trading_ai.deployment final-report
   Artifact: ``data/deployment/final_readiness_report.txt``
   Pass: ``ready_for_first_20`` = true in ``final_readiness.json`` AND report says READY.

Exact commands (copy-paste, from ``trading-ai`` after ``cd``):
-------------------------------------------------------------
cd trading-ai && PYTHONPATH=src python3 -m trading_ai.deployment checklist
PYTHONPATH=src python3 -m trading_ai.deployment micro-validation --n 3
PYTHONPATH=src python3 -m trading_ai.deployment readiness
PYTHONPATH=src python3 -m trading_ai.deployment final-report
"""
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    if write_file:
        runtime_proof_runbook_path().write_text(text, encoding="utf-8")
    return text
