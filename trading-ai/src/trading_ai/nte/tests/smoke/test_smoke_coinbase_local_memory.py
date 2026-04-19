"""Skeleton smoke: Coinbase local memory path."""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="connect coinbase_memory_adapter")
def test_smoke_coinbase_memory_path() -> None:
    assert False
