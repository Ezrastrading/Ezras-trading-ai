"""
Deployment proof runners — checklist, live micro-validation streak, final readiness.

Commands::

    python -m trading_ai.deployment checklist
    python -m trading_ai.deployment micro-validation --n 3
    python -m trading_ai.deployment readiness
    python -m trading_ai.deployment final-report

"""

from trading_ai.deployment.deployment_checklist import run_deployment_checklist
from trading_ai.deployment.deployment_parity import run_deployment_parity_report
from trading_ai.deployment.final_readiness_report import write_final_readiness_report
from trading_ai.deployment.runtime_proof_runbook import write_runtime_proof_runbook
from trading_ai.deployment.first_20_protocol import evaluate_first_20_protocol_readiness
from trading_ai.deployment.live_micro_validation import run_live_micro_validation_streak
from trading_ai.deployment.readiness_decision import compute_final_readiness
from trading_ai.deployment.supabase_schema_readiness import run_supabase_schema_readiness

__all__ = [
    "run_deployment_checklist",
    "run_live_micro_validation_streak",
    "compute_final_readiness",
    "evaluate_first_20_protocol_readiness",
    "run_supabase_schema_readiness",
    "run_deployment_parity_report",
    "write_final_readiness_report",
    "write_runtime_proof_runbook",
]
