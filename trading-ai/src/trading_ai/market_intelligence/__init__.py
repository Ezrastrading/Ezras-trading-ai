"""Read-only market intelligence (no execution hooks)."""

from trading_ai.market_intelligence.market_intelligence_engine import (
    active_markets_snapshot_path,
    get_active_markets,
)

__all__ = ["active_markets_snapshot_path", "get_active_markets"]
