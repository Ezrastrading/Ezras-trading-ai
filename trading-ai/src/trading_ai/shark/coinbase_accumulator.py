"""
Coinbase **Avenue A — NexTrading Engine (NTE)**.

Legacy gate/swing execution (Gates A–C) has been removed. All Coinbase logic lives in
``trading_ai.nte`` (execution, memory, learning, CEO, rewards, goals, research).

State: ``shark/state/nte_coinbase_positions.json``; memory: ``shark/nte/memory/``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from trading_ai.nte.execution.coinbase_engine import CoinbaseNTEngine, coinbase_nt_enabled
from trading_ai.nte.execution.coinbase_sizing import (
    _PRODUCT_BASE_PRECISION,
    _enforce_min_base_for_sell,
    _fmt_base_size,
    _min_base_size_for_product,
)
from trading_ai.nte.execution.state import load_state
from trading_ai.shark.outlets.coinbase import CoinbaseClient

logger = logging.getLogger(__name__)

__all__ = [
    "CoinbaseAccumulator",
    "coinbase_enabled",
    "load_coinbase_state",
    "force_sell_all_positions",
    "sell_expired_positions_on_startup",
    "_PRODUCT_BASE_PRECISION",
    "_enforce_min_base_for_sell",
    "_fmt_base_size",
    "_min_base_size_for_product",
]


def coinbase_enabled() -> bool:
    return coinbase_nt_enabled()


def load_coinbase_state() -> Dict[str, Any]:
    return load_state()


def force_sell_all_positions() -> int:
    """No-op — old startup flatten removed (use Coinbase UI if you must flatten)."""
    return 0


def sell_expired_positions_on_startup() -> int:
    """NTE uses spot with time stops — no option-style expiry."""
    return 0


class CoinbaseAccumulator:
    """Thin adapter so ``run_shark`` and scripts keep a stable import path."""

    def __init__(self, client: Optional[CoinbaseClient] = None) -> None:
        self._engine = CoinbaseNTEngine(client=client)
        self._client = self._engine._client

    def scan_and_trade(self) -> None:
        """Scheduled ~5m: new signals + pending limit management (exits run on fast tick)."""
        self._engine.run_slow_tick()

    def _run_exits_only(self) -> None:
        """Scheduled ~NTE_FAST_TICK_SECONDS: exits + stale limit cancels."""
        self._engine.run_fast_tick()

    def dawn_sweep_gate_a(self) -> int:
        return self._engine.dawn_sweep_gate_a()

    def load_and_check_positions_on_startup(self) -> None:
        self._engine.load_state_and_reconcile()

    def get_summary(self) -> Dict[str, Any]:
        return self._engine.get_summary()

    def run_full_cycle(self) -> None:
        """Fast + slow (manual / one-shot)."""
        self._engine.run_cycle()


def main() -> None:
    """``python -m trading_ai.shark.coinbase_accumulator`` — one full NTE cycle (if enabled)."""
    logging.basicConfig(level=logging.INFO)
    if not coinbase_enabled():
        logger.info("COINBASE_ENABLED is not true — exit")
        return
    CoinbaseAccumulator().run_full_cycle()


if __name__ == "__main__":
    main()
