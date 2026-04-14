"""Encryption-at-rest helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cryptography.fernet import Fernet

from trading_ai.security import encryption_at_rest as ear


def test_status_unencrypted_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_STATE_ENCRYPTION_KEY", raising=False)
    st = ear.encryption_status()
    assert st["encryption_key_configured"] is False
    assert st["explicit_state"] == "unencrypted_explicit"


def test_round_trip_with_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("EZRAS_STATE_ENCRYPTION_KEY", key)
    p = tmp_path / "x.json"
    ear.encrypt_json_file(p, {"a": 1})
    obj = ear.read_json_maybe_encrypted(p)
    assert obj == {"a": 1}
    st = ear.encryption_status()
    assert st["encryption_enabled"] is True


def test_operational_explicit_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EZRAS_STATE_ENCRYPTION_KEY", raising=False)
    st = ear.encryption_operational_status()
    assert st["operational_class"] == "encryption_explicitly_disabled"
    assert st["operational_verification"]["operational_class"] == "encryption_explicitly_disabled"


def test_operational_verified_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("EZRAS_STATE_ENCRYPTION_KEY", key)
    st = ear.encryption_operational_status()
    assert st["operational_class"] == "encryption_available_and_verified"
    assert st["operational_verification"]["verified"] is True


def test_operational_misconfigured_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_STATE_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    st = ear.encryption_operational_status()
    assert st["operational_class"] == "encryption_misconfigured"
