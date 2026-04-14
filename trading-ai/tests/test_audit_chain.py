"""Tamper-evident audit chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.governance.audit_chain import append_chained_event, verify_audit_chain


def test_chain_append_verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "logs" / "governance_audit_chain.jsonl"
    append_chained_event({"k": 1}, chain_file=p)
    append_chained_event({"k": 2}, chain_file=p)
    vr = verify_audit_chain(p)
    assert vr.ok is True
    assert vr.records_verified == 2


def test_tamper_fails_full_integrity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "logs" / "governance_audit_chain.jsonl"
    append_chained_event({"x": "a"}, chain_file=p)
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw.replace('"a"', '"b"'), encoding="utf-8")
    from trading_ai.governance.consistency_engine import get_full_integrity_report

    rep = get_full_integrity_report()
    assert rep["overall_ok"] is False
    assert rep["audit_chain"]["tamper_evident_failure"] is True


def test_tamper_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "logs" / "governance_audit_chain.jsonl"
    append_chained_event({"x": "a"}, chain_file=p)
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw.replace('"a"', '"b"'), encoding="utf-8")
    vr = verify_audit_chain(p)
    assert vr.ok is False
