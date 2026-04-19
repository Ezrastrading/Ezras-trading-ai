"""First-20 shadow session harness (accelerated: few trades)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session
from trading_ai.runtime_proof.first_twenty_session import FirstTwentySessionConfig, run_first_twenty_shadow_session


def test_first_twenty_simulation_smoke(tmp_path: Path) -> None:
    cfg = FirstTwentySessionConfig(runtime_root=tmp_path, max_completed_trades=3)
    out = run_first_twenty_shadow_session(cfg, simulate_trades=3)
    assert out.get("status") in ("completed", "aborted_rollback")
    assert out.get("recommendation") == "PASS_SHADOW_VERIFICATION"
    arch = Path((out.get("manifest") or {}).get("artifact_archive") or "")
    assert arch.is_dir()
    assert (arch / "session_manifest.json").is_file()
    j = judge_first_twenty_session(arch)
    assert j.get("session_completeness", {}).get("completed_trades") == 3
