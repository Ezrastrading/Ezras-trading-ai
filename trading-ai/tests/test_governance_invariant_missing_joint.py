"""Invariant: enforcement + MISSING_JOINT_BLOCKS cannot allow empty/missing joint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full


def test_missing_joint_fail_closed_under_enforcement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "rt"
    gdir = root / "shark" / "memory" / "global"
    gdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_MISSING_JOINT_BLOCKS", "true")
    # no joint file → present False
    ok, reason, _ = check_new_order_allowed_full(
        venue="coinbase",
        operation="test",
        log_decision=False,
    )
    assert ok is False
    assert "missing_joint" in reason


def test_invariant_raises_if_broken_decide(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If _decide incorrectly allowed with empty joint, check_new_order_allowed_full must assert."""
    root = tmp_path / "rt2"
    (root / "shark" / "memory" / "global").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("GOVERNANCE_MISSING_JOINT_BLOCKS", "true")
    p = root / "shark" / "memory" / "global" / "joint_review_latest.json"
    p.write_text(json.dumps({"empty": True, "joint_review_id": ""}), encoding="utf-8")

    import trading_ai.global_layer.governance_order_gate as gog

    def _bad_decide(snap):  # type: ignore[no-untyped-def]
        return True, "broken"

    monkeypatch.setattr(gog, "_decide", _bad_decide)
    with pytest.raises(AssertionError, match="FAIL-CLOSED VIOLATION"):
        check_new_order_allowed_full(venue="coinbase", operation="test", log_decision=False)
