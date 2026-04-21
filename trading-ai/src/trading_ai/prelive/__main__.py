"""Run full prelive sequence (importable)."""

from __future__ import annotations

import os
from pathlib import Path

from trading_ai.prelive import avenue_auto_attach_proof
from trading_ai.prelive import future_avenue_auto_assignment_proof
from trading_ai.prelive import deployment_truth_audit
from trading_ai.prelive import execution_friction_lab
from trading_ai.prelive import gate_b_staged_validation
from trading_ai.prelive import mock_execution_harness
from trading_ai.prelive import operator_interpretation_audit
from trading_ai.prelive import prelive_lock_report
from trading_ai.prelive import reality_proving_matrix
from trading_ai.prelive import sizing_calibration_sandbox
from trading_ai.prelive.execution_mirror import run as execution_mirror_run
from trading_ai.prelive.go_no_go import run as go_no_go_run
from trading_ai.prelive.honesty_enforcement import run as honesty_run
from trading_ai.runtime_paths import ezras_runtime_root


def main() -> None:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    root = Path(raw).expanduser().resolve() if raw else ezras_runtime_root()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    reality_proving_matrix.run(runtime_root=root)
    execution_mirror_run(runtime_root=root)
    mock_execution_harness.run(runtime_root=root)
    execution_friction_lab.run(runtime_root=root)
    sizing_calibration_sandbox.run(runtime_root=root)
    gate_b_staged_validation.run(runtime_root=root)
    operator_interpretation_audit.run(runtime_root=root)
    deployment_truth_audit.run(runtime_root=root)
    avenue_auto_attach_proof.run(runtime_root=root)
    future_avenue_auto_assignment_proof.run(runtime_root=root)
    honesty_run(runtime_root=root)
    go_no_go_run(runtime_root=root)
    prelive_lock_report.run(runtime_root=root)


if __name__ == "__main__":
    main()
