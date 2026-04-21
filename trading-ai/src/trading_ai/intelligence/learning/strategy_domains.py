"""Strategy-oriented cross-links for learning synthesis."""

from __future__ import annotations

STRATEGY_CROSSWALK: dict[str, list[str]] = {
    "regime_detection": ["edge_validation", "market_microstructure"],
    "edge_validation": ["gate_behavior", "venue_behavior"],
}
