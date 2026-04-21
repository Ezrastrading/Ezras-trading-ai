"""First-20 diagnostic phase — contract strings and defaults (avenue-agnostic)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class PhaseStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE_DIAGNOSTIC = "ACTIVE_DIAGNOSTIC"
    PAUSED_REVIEW_REQUIRED = "PAUSED_REVIEW_REQUIRED"
    PASSED_READY_FOR_NEXT_PHASE = "PASSED_READY_FOR_NEXT_PHASE"
    FAILED_REWORK_REQUIRED = "FAILED_REWORK_REQUIRED"


class CautionLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


# Relative to EZRAS_RUNTIME_ROOT
P_TRUTH = "data/control/first_20_truth.json"
P_DIAGNOSTICS = "data/deployment/first_20_trade_diagnostics.jsonl"
P_SCOREBOARD_JSON = "data/control/first_20_scoreboard.json"
P_SCOREBOARD_TXT = "data/control/first_20_scoreboard.txt"
P_EXEC_QUALITY = "data/control/first_20_execution_quality.json"
P_EDGE_QUALITY = "data/control/first_20_edge_quality.json"
P_ADJUSTMENTS = "data/control/first_20_adjustments.jsonl"
P_PAUSE_REASON = "data/control/first_20_pause_reason.json"
P_PASS_DECISION = "data/control/first_20_pass_decision.json"
P_LESSONS_JSON = "data/learning/first_20_lessons.json"
P_LESSONS_TXT = "data/learning/first_20_lessons.txt"
P_OPERATOR_JSON = "data/reports/first_20_operator_report.json"
P_OPERATOR_TXT = "data/reports/first_20_operator_report.txt"
P_REBUY_AUDIT = "data/control/first_20_rebuy_audit.json"
P_FINAL_JSON = "data/control/first_20_final_truth.json"
P_FINAL_TXT = "data/control/first_20_final_truth.txt"
P_OPERATOR_ACK = "data/control/first_20_operator_evidence_ack.json"

ENV_DIAGNOSTIC_ACTIVE = "FIRST_20_DIAGNOSTIC_PHASE_ACTIVE"
ENV_MAX_DD = "FIRST_20_MAX_DRAWDOWN_USD"
ENV_OPERATOR_ACK_HOURS = "FIRST_20_OPERATOR_ACK_MAX_AGE_HOURS"


def default_truth_contract() -> Dict[str, Any]:
    """Honest cold start — nothing proven, not ready."""
    return {
        "phase_status": PhaseStatus.NOT_STARTED.value,
        "trades_completed": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "avg_pnl_per_trade": 0.0,
        "expectancy_per_trade": 0.0,
        "max_consecutive_losses": 0,
        "max_drawdown_seen": 0.0,
        "duplicate_blocks_seen": 0,
        "governance_blocks_seen": 0,
        "adaptive_brakes_seen": 0,
        "venue_rejects_seen": 0,
        "partial_failure_count": 0,
        "logging_failures_seen": 0,
        "rebuy_block_failures_seen": 0,
        "strategy_mix": {},
        "gate_mix": {},
        "avenue_mix": {},
        "ready_for_next_phase": False,
        "exact_reason_if_not_ready": "Diagnostic phase not started or insufficient coherent runtime evidence.",
        "automation_state": {
            "size_multiplier": 1.0,
            "paused_strategy_ids": [],
            "paused_gate_ids": [],
            "rebuy_tightened": False,
            "operator_review_required": False,
            "max_simultaneous_exposure_cap": None,
            "caution_level": CautionLevel.GREEN.value,
        },
        "runtime_counters": {
            "consecutive_integrity_failures": 0,
            "consecutive_logging_failures": 0,
            "failure_signature_counts": {},
            "venue_reject_streak": 0,
        },
        "meta": {
            "schema": "first_20_truth_v1",
            "honesty_note": "Counts and readiness are recomputed from diagnostics; not inferred from file existence alone.",
        },
    }


def default_rebuy_audit() -> Dict[str, Any]:
    return {
        "rebuy_attempts": 0,
        "rebuy_allowed_count": 0,
        "rebuy_blocked_count": 0,
        "rebuy_block_reasons": {},
        "any_rebuy_before_log_completion": False,
        "any_rebuy_before_exit_truth": False,
        "rebuy_contract_clean": True,
    }
