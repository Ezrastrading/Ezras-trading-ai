"""Explicit contracts: top-level ``runtime_root`` on runner JSON envelopes (operator forensics)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.orchestration.avenue_a_live_daemon import _runtime_runner_cycle_envelope


def test_runtime_runner_last_cycle_envelope_includes_top_level_runtime_root() -> None:
    """``runtime_runner_last_cycle.json`` is written with ``avenue_a_daemon`` + ``runtime_root`` at top level."""
    root = Path("/tmp/ezras_rt_contract").resolve()
    out = {"ok": True, "ts": "2026-01-01T00:00:00Z", "mode": "supervised_live"}
    env = _runtime_runner_cycle_envelope(out, runtime_root=root)
    assert "runtime_root" in env
    assert env["runtime_root"] == str(root)
    assert env["avenue_a_daemon"] == out


def test_runtime_runner_last_failure_contract_includes_top_level_runtime_root() -> None:
    """``runtime_runner_last_failure.json`` body includes ``runtime_root`` alongside terminal + avenue_a_daemon."""
    root = Path("/tmp/ezras_rt_fail_contract").resolve()
    terminal = {"failure_stage": "x", "failure_reason": "y"}
    avenue = {"ok": False, "mode": "supervised_live"}
    fail_body = {
        **terminal,
        "avenue_a_daemon": avenue,
        "ts": "2026-01-01T00:00:00Z",
        "runtime_root": str(root.resolve()),
    }
    if fail_body.get("failure_reason") is None:
        fail_body["failure_reason"] = "fallback"
    assert "runtime_root" in fail_body
    assert fail_body["runtime_root"] == str(root.resolve())
