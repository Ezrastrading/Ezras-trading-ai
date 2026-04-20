"""Primary CLI SSL policy — exempt local/bootstrap commands only."""

from __future__ import annotations

import ssl

import pytest

from trading_ai.runtime_checks.cli_ssl_policy import enforce_ssl_for_primary_cli_command


def test_enforce_ssl_skips_validate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def _fake() -> None:
        called.append(True)

    monkeypatch.setattr("trading_ai.runtime_checks.cli_ssl_policy.enforce_ssl", _fake)
    enforce_ssl_for_primary_cli_command("validate-env")
    assert called == []


def test_enforce_ssl_runs_for_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def _fake() -> None:
        called.append(True)

    monkeypatch.setattr("trading_ai.runtime_checks.cli_ssl_policy.enforce_ssl", _fake)
    enforce_ssl_for_primary_cli_command("run")
    assert called == [True]


def test_enforce_ssl_passes_through_on_host_openssl() -> None:
    enforce_ssl_for_primary_cli_command("run")
    assert "libressl" not in ssl.OPENSSL_VERSION.lower()
