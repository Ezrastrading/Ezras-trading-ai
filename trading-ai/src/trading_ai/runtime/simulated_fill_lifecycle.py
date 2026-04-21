"""
Backward-compatible shim: simulated fill lifecycle lives in ``trading_ai.simulation``.
"""

from trading_ai.simulation.fill_lifecycle import advance_simulated_fill_once

__all__ = ["advance_simulated_fill_once"]
