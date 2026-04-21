"""Daemon verification matrix + agnostic harness (fake / replay / live-proof scan)."""

from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_verification_artifacts
from trading_ai.daemon_testing.daemon_matrix_runner import run_daemon_verification_matrix

__all__ = [
    "run_daemon_verification_matrix",
    "write_daemon_verification_artifacts",
]
