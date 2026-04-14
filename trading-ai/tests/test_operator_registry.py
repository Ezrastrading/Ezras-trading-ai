"""Operator / doctrine registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.governance.operator_registry import (
    approve_doctrine,
    load_registry,
    register_operator,
    verify_doctrine_with_registry,
)


def test_register_and_approve_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    r1 = register_operator(operator_id="op1", role="owner")
    assert r1["ok"] is True
    a = approve_doctrine(operator_id="op1", doctrine_version="2026.04.13", notes="test")
    assert a["ok"] is True
    st = load_registry()
    assert any(d.get("status") == "active" for d in st["doctrine_approvals"])
    v = verify_doctrine_with_registry()
    assert v["ok"] is True
    assert v["mode"] == "registry_approved"


def test_registry_required_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EZRAS_DOCTRINE_REGISTRY_REQUIRED", "1")
    v = verify_doctrine_with_registry()
    assert v["ok"] is False
    assert v["mode"] == "registry_required_missing_approval"
