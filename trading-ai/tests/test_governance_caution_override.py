"""GOVERNANCE_CAUTION_BLOCK_ENTRIES gates whether caution mode blocks entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full


def _write_joint(gdir: Path, *, mode: str = "caution") -> None:
    payload = {
        "joint_review_id": "jr_caution_override",
        "live_mode_recommendation": mode,
        "review_integrity_state": "full",
        "generated_at": "2099-06-01T12:00:00+00:00",
        "packet_id": "pkt_co",
        "empty": False,
    }
    (gdir / "joint_review_latest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_caution_allows_when_block_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.delenv("GOVERNANCE_CAUTION_BLOCK_ENTRIES", raising=False)
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="caution")
    ok, reason, audit = check_new_order_allowed_full(venue="coinbase", operation="new_entry", log_decision=False)
    assert ok is True
    assert reason == "joint_review_caution_allowed"
    assert audit["live_mode"] == "caution"


def test_caution_blocks_when_block_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_CAUTION_BLOCK_ENTRIES", "true")
    gdir = tmp_path / "shark" / "memory" / "global"
    gdir.mkdir(parents=True)
    _write_joint(gdir, mode="caution")
    ok, reason, _ = check_new_order_allowed_full(venue="coinbase", operation="new_entry", log_decision=False)
    assert ok is False
    assert reason == "joint_review_caution_blocked"
