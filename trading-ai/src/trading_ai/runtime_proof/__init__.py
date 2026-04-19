"""Runtime proof harnesses (shadow/paper; no live capital)."""

from trading_ai.runtime_proof.coinbase_avenue_a_blocker_closure import run_blocker_closure_bundle
from trading_ai.runtime_proof.coinbase_shadow_paper_pass import run_full_proof
from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session, write_judge_report
from trading_ai.runtime_proof.first_twenty_session import (
    FirstTwentySessionConfig,
    RollbackThresholds,
    run_first_twenty_shadow_session,
    run_preflight,
)

__all__ = [
    "run_full_proof",
    "run_blocker_closure_bundle",
    "FirstTwentySessionConfig",
    "RollbackThresholds",
    "run_preflight",
    "run_first_twenty_shadow_session",
    "judge_first_twenty_session",
    "write_judge_report",
]
