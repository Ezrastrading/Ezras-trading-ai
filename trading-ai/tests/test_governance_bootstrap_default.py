"""Default joint bootstrap allows enforcement without a pre-seeded joint file."""

from __future__ import annotations

import pytest

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full


def test_bootstrap_writes_and_allows_under_enforcement(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "shark" / "memory" / "global").mkdir(parents=True, exist_ok=True)
    ok, reason, audit = check_new_order_allowed_full(venue="coinbase", operation="test", log_decision=False)
    assert ok is True
    assert reason == "joint_review_normal"
    assert audit.get("reason_code") == "joint_review_normal"
    jp = tmp_path / "shark" / "memory" / "global" / "joint_review_latest.json"
    assert jp.is_file()
    assert "bootstrap_safe_default" in jp.read_text(encoding="utf-8")
