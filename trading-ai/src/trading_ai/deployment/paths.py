"""Deployment proof artifacts under ``EZRAS_RUNTIME_ROOT/data/deployment``."""

from __future__ import annotations

from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def deployment_data_dir() -> Path:
    p = ezras_runtime_root() / "data" / "deployment"
    p.mkdir(parents=True, exist_ok=True)
    return p


def control_data_dir() -> Path:
    """Operator control artifacts: ``EZRAS_RUNTIME_ROOT/data/control``."""
    p = ezras_runtime_root() / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def live_validation_runs_dir() -> Path:
    p = deployment_data_dir() / "live_validation_runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def streak_state_path() -> Path:
    return deployment_data_dir() / "live_validation_streak.json"


def checklist_json_path() -> Path:
    return deployment_data_dir() / "deployment_checklist.json"


def checklist_txt_path() -> Path:
    return deployment_data_dir() / "deployment_checklist.txt"


def final_readiness_path() -> Path:
    return deployment_data_dir() / "final_readiness.json"


def soak_report_path() -> Path:
    return deployment_data_dir() / "soak_report.json"


def ops_outputs_proof_path() -> Path:
    return deployment_data_dir() / "ops_outputs_proof.json"


def env_parity_report_path() -> Path:
    return deployment_data_dir() / "env_parity_report.json"


def governance_proof_path() -> Path:
    return deployment_data_dir() / "governance_proof.json"


def reconciliation_proof_jsonl_path() -> Path:
    return deployment_data_dir() / "reconciliation_proof.jsonl"


def supabase_proof_jsonl_path() -> Path:
    return deployment_data_dir() / "supabase_proof.jsonl"


def first_20_protocol_json_path() -> Path:
    return deployment_data_dir() / "first_20_protocol.json"


def first_20_protocol_txt_path() -> Path:
    return deployment_data_dir() / "first_20_protocol.txt"


def supabase_schema_readiness_path() -> Path:
    return deployment_data_dir() / "supabase_schema_readiness.json"


def deployment_parity_report_path() -> Path:
    return deployment_data_dir() / "deployment_parity_report.json"


def final_readiness_report_txt_path() -> Path:
    return deployment_data_dir() / "final_readiness_report.txt"


def runtime_proof_runbook_path() -> Path:
    return deployment_data_dir() / "runtime_proof_runbook.txt"
