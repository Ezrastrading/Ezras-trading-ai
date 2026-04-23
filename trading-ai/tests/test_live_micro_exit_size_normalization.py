from __future__ import annotations

import pytest


def test_exit_size_snaps_down_to_increment() -> None:
    from trading_ai.live_micro.exit_size import normalize_exit_base_size

    s, diag = normalize_exit_base_size(base_qty=0.0000200003, base_increment="0.00000001", base_min_size="0.00001")
    assert diag["reason"] == "ok"
    assert s is not None
    # snapped down: should be <= original
    assert float(s) <= 0.0000200003


def test_exit_size_does_not_round_up() -> None:
    from trading_ai.live_micro.exit_size import normalize_exit_base_size

    s, _ = normalize_exit_base_size(base_qty=1.23456789, base_increment="0.1", base_min_size="0.1")
    assert s == "1.2"


def test_exit_size_invalid_when_below_min_after_snap() -> None:
    from trading_ai.live_micro.exit_size import normalize_exit_base_size

    s, diag = normalize_exit_base_size(base_qty=0.0000100001, base_increment="0.00000001", base_min_size="0.00002")
    assert s is None
    assert diag["reason"] == "below_base_min_after_snap"

