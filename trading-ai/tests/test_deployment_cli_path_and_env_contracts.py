"""Regression: deployment __main__ Path shadowing; operator env contracts; orchestration-status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_DEPLOYMENT_MAIN = _SRC / "trading_ai" / "deployment" / "__main__.py"

_TARGETED_IMPORT_HYGIENE = (
    _DEPLOYMENT_MAIN,
    _SRC / "trading_ai" / "orchestration" / "avenue_a_live_daemon.py",
    _SRC / "trading_ai" / "runtime_proof" / "live_execution_validation.py",
    _SRC / "trading_ai" / "orchestration" / "supervised_avenue_a_truth.py",
    _SRC / "trading_ai" / "deployment" / "autonomous_smoke.py",
)


def _json_from_stdout(stdout: str) -> dict:
    i = stdout.find("{")
    if i < 0:
        raise AssertionError("no JSON object in stdout: " + stdout[:500])
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(stdout[i:])
    return obj


def test_deployment_main_has_no_inner_pathlib_import() -> None:
    """Regression: inner `from pathlib import Path` in main() caused Path shadowing / UnboundLocalError."""
    text = _DEPLOYMENT_MAIN.read_text(encoding="utf-8")
    bad = [i + 1 for i, ln in enumerate(text.splitlines()) if ln.lstrip().startswith("from pathlib import Path") and ln[:1] in (" ", "\t")]
    assert not bad, f"indented pathlib import(s) in deployment __main__.py at lines: {bad}"


def test_targeted_files_no_indented_pathlib_import() -> None:
    for path in _TARGETED_IMPORT_HYGIENE:
        text = path.read_text(encoding="utf-8")
        bad = [i + 1 for i, ln in enumerate(text.splitlines()) if ln.lstrip().startswith("from pathlib import Path") and ln[:1] in (" ", "\t")]
        assert not bad, f"indented pathlib import in {path.relative_to(_REPO_ROOT)} at lines: {bad}"


def _run_dep(args: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **env}
    merged["PYTHONPATH"] = str(_SRC)
    return subprocess.run(
        [sys.executable, "-m", "trading_ai.deployment", *args],
        cwd=str(_REPO_ROOT),
        env=merged,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_check_env_subcommand_prints_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COINBASE_API_KEY_NAME", raising=False)
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    cp = _run_dep(["check-env"])
    assert cp.returncode == 12
    assert "COINBASE_API_KEY_NAME" in cp.stdout
    assert "MISSING" in cp.stdout
    assert "coinbase_credentials_not_configured" in cp.stdout
    assert "next_step:" in cp.stdout


def test_refresh_runtime_artifacts_no_path_unbound_local() -> None:
    cp = _run_dep(["refresh-runtime-artifacts", "--show-stale-only"])
    assert cp.returncode == 0, cp.stderr + cp.stdout
    body = _json_from_stdout(cp.stdout)
    assert "runtime_artifact_refresh_truth" in body


def test_orchestration_status_with_backbone_no_crash() -> None:
    cp = _run_dep(["orchestration-status", "--with-backbone"])
    assert cp.returncode == 0, cp.stderr + cp.stdout
    out = _json_from_stdout(cp.stdout)
    assert "truth_chain_summary" in out
    assert "operator_orchestration_path_summary" in out
    assert "autonomous_backbone_status" in out


def test_supervised_confirm_contract_export_roundtrips() -> None:
    from trading_ai.deployment.operator_env_contracts import (
        LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM,
        LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE,
        supervised_gate_a_live_validation_confirm_contract,
    )

    c = supervised_gate_a_live_validation_confirm_contract()
    assert c["required_env"] == LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM
    assert c["required_value_exact"] == LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE
    assert LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE in c["export_command"]


def test_classify_missing_supervised_confirm_rich_reason() -> None:
    from trading_ai.runtime_proof.live_validation_terminal_failure import (
        FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED,
        FAILURE_STAGE_PRE_BUY,
        classify_early_guard_failure,
    )

    err = (
        "missing_or_invalid_LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_or_autonomous_ack "
        "(daemon_active=True)"
    )
    code, stage, reason = classify_early_guard_failure(err)
    assert code == FAILURE_CODE_PROOF_CONTRACT_NOT_SATISFIED
    assert stage == FAILURE_STAGE_PRE_BUY
    assert "export" in reason.lower() or "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM" in reason


def test_classify_coinbase_credentials_not_configured() -> None:
    from trading_ai.runtime_proof.live_validation_terminal_failure import (
        FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED,
        FAILURE_STAGE_PRE_BUY,
        classify_early_guard_failure,
    )

    code, stage, _ = classify_early_guard_failure("coinbase_auth_failure:Coinbase credentials not configured")
    assert code == FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED
    assert stage == FAILURE_STAGE_PRE_BUY


def test_missing_coinbase_env_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.deployment import operator_env_contracts as oec

    for name in (
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_KEY",
        "COINBASE_API_PRIVATE_KEY",
        "COINBASE_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    miss = oec.missing_coinbase_credential_env_vars()
    assert set(miss) == {
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_KEY",
        "COINBASE_API_PRIVATE_KEY",
        "COINBASE_API_SECRET",
    }


def test_supervised_truth_distinguishes_last_success_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.orchestration.supervised_avenue_a_truth import write_avenue_a_supervised_live_truth

    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "execution_proof").mkdir(parents=True)
    bad_proof = {
        "FINAL_EXECUTION_PROVEN": False,
        "failure_stage": "pre_buy",
        "failure_code": "proof_contract_not_satisfied",
        "runtime_root": str(tmp_path.resolve()),
    }
    (tmp_path / "execution_proof" / "live_execution_validation.json").write_text(
        json.dumps(bad_proof), encoding="utf-8"
    )
    meta = {
        "truth_version": "gate_a_last_successful_live_proof_meta_v1",
        "last_successful_trade_id": "live_exec_abc",
        "last_successful_at": "2026-01-01T00:00:00+00:00",
        "runtime_root": str(tmp_path.resolve()),
        "FINAL_EXECUTION_PROVEN": True,
    }
    (tmp_path / "data" / "control" / "gate_a_last_successful_live_proof_meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    (tmp_path / "data" / "control" / "avenue_a_supervised_trade_log.jsonl").write_text("", encoding="utf-8")

    out = write_avenue_a_supervised_live_truth(runtime_root=tmp_path)
    assert out.get("last_successful_full_gate_a_trade_id") == "live_exec_abc"
    assert out.get("latest_attempt_failed_pre_execution") is True
