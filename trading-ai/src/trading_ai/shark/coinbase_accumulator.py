"""
Public-safe Coinbase accumulator shim.

The full live execution outlets (Coinbase auth/client) live in the private repo. The public
repo keeps this module so smoke tests and import paths remain stable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["CoinbaseAccumulator", "coinbase_enabled", "load_coinbase_state"]


def coinbase_enabled() -> bool:
    try:
        from trading_ai.nte.execution.coinbase_engine import coinbase_nt_enabled

        return bool(coinbase_nt_enabled())
    except Exception:
        return False


def load_coinbase_state() -> Dict[str, Any]:
    try:
        from trading_ai.nte.execution.state import load_state

        out = load_state()
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


class CoinbaseAccumulator:
    """
    Thin adapter so `master_smoke_test.py` and public tooling can import the symbol.

    In public builds, methods are safe to call only if the private Coinbase outlet stack is
    present; otherwise they raise a clear runtime error.
    """

    def __init__(self, client: Optional[object] = None) -> None:
        self._client = client
        self._engine = None

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from trading_ai.nte.execution.coinbase_engine import CoinbaseNTEngine
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "CoinbaseAccumulator requires trading_ai.nte.execution.coinbase_engine, which is missing."
            ) from e
        self._engine = CoinbaseNTEngine(client=self._client)  # type: ignore[arg-type]
        return self._engine

    def run_full_cycle(self) -> None:
        eng = self._ensure_engine()
        eng.run_cycle()

    def scan_and_trade(self) -> None:
        eng = self._ensure_engine()
        eng.run_slow_tick()

