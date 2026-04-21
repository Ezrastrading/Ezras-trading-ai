from __future__ import annotations

import os
from pathlib import Path


def test_deploy_preflight_build_report_ok(tmp_path: Path, monkeypatch) -> None:
    # Build a minimal fake /opt layout inside tmp_path
    public = tmp_path / "opt" / "ezra-public"
    private = tmp_path / "opt" / "ezra-private"
    runtime = tmp_path / "opt" / "ezra-runtime"
    venv = tmp_path / "opt" / "ezra-venv"

    (public / "trading-ai" / "docs" / "systemd").mkdir(parents=True, exist_ok=True)
    (private / "trading-ai").mkdir(parents=True, exist_ok=True)
    (runtime / "env").mkdir(parents=True, exist_ok=True)
    (runtime / "env" / "common.env").write_text("NTE_EXECUTION_MODE=paper\n", encoding="utf-8")
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    # unit templates exist for preflight check
    (public / "trading-ai" / "docs" / "systemd" / "ezra-ops.service").write_text("x", encoding="utf-8")
    (public / "trading-ai" / "docs" / "systemd" / "ezra-research.service").write_text("x", encoding="utf-8")

    # Ensure live is disabled in env
    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")

    from scripts.server.deploy_preflight import build_report, resolve_paths

    p = resolve_paths(
        public_root=str(public),
        private_root=str(private),
        runtime_root=str(runtime),
        venv_root=str(venv),
    )

    rep = build_report(p)
    assert rep["truth_version"] == "deploy_preflight_v1"
    assert rep["paths"]["runtime_root"] == str(runtime.resolve())
    assert "checks" in rep
    assert "filesystem" in rep["checks"]
    assert "systemd_unit_templates" in rep["checks"]
    assert rep["checks"]["live_disabled"]["ok"] is True


def test_deploy_preflight_live_enabled_fails(monkeypatch) -> None:
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    from scripts.server.deploy_preflight import assert_live_disabled

    ok, errs = assert_live_disabled()
    assert ok is False
    assert "NTE_EXECUTION_MODE_is_live" in errs

