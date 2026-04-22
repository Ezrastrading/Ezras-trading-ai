"""
Deployment proof runners — checklist, live micro-validation streak, final readiness.

Commands::

    python -m trading_ai.deployment checklist
    python -m trading_ai.deployment micro-validation --n 3
    python -m trading_ai.deployment readiness
    python -m trading_ai.deployment final-report

Heavy submodules load lazily so ``import trading_ai.deployment`` works under dual-repo
PYTHONPATH overlays (private may omit optional shark helpers).
"""

from __future__ import annotations

import importlib
from typing import Any

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

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "run_deployment_checklist": ("trading_ai.deployment.deployment_checklist", "run_deployment_checklist"),
    "run_deployment_parity_report": ("trading_ai.deployment.deployment_parity", "run_deployment_parity_report"),
    "write_final_readiness_report": ("trading_ai.deployment.final_readiness_report", "write_final_readiness_report"),
    "write_runtime_proof_runbook": ("trading_ai.deployment.runtime_proof_runbook", "write_runtime_proof_runbook"),
    "evaluate_first_20_protocol_readiness": ("trading_ai.deployment.first_20_protocol", "evaluate_first_20_protocol_readiness"),
    "run_live_micro_validation_streak": ("trading_ai.deployment.live_micro_validation", "run_live_micro_validation_streak"),
    "compute_final_readiness": ("trading_ai.deployment.readiness_decision", "compute_final_readiness"),
    "run_supabase_schema_readiness": ("trading_ai.deployment.supabase_schema_readiness", "run_supabase_schema_readiness"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod_name, attr = _LAZY_EXPORTS[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
