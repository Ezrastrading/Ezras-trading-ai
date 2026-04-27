"""Edge + learning system: track edge metrics and build win/loss patterns."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EdgeMetrics:
    """Edge metrics for a trade."""
    edge_percent: float
    confidence_score: float
    liquidity_score: float
    fill_rate: float


@dataclass
class TradePattern:
    """Trade pattern for learning."""
    edge_percent: float
    confidence_score: float
    liquidity_score: float
    outcome: str  # "win" or "loss"
    pnl: float
    timestamp: float
    market_category: str


class EdgeLearningSystem:
    """Track edge metrics and build win/loss patterns."""
    
    def __init__(self):
        self._memory_dir = Path(os.environ.get("EZRAS_RUNTIME_ROOT", "/app/ezras-runtime")) / "shark/memory"
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._edge_file = self._memory_dir / "edge_registry.json"
        self._patterns_file = self._memory_dir / "win_loss_patterns.json"
        self._edge_registry: Dict[str, EdgeMetrics] = {}
        self._patterns: List[TradePattern] = []
        self._load_edge_registry()
        self._load_patterns()
    
    def _load_edge_registry(self) -> None:
        """Load edge registry from file."""
        try:
            if self._edge_file.exists():
                with open(self._edge_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, value in data.items():
                        self._edge_registry[key] = EdgeMetrics(**value)
        except Exception as exc:
            logger.warning("Failed to load edge registry: %s", exc)
    
    def _save_edge_registry(self) -> None:
        """Save edge registry to file."""
        try:
            with open(self._edge_file, "w", encoding="utf-8") as f:
                json.dump(
                    {k: asdict(v) for k, v in self._edge_registry.items()},
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.error("Failed to save edge registry: %s", exc)
    
    def _load_patterns(self) -> None:
        """Load win/loss patterns from file."""
        try:
            if self._patterns_file.exists():
                with open(self._patterns_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._patterns = [TradePattern(**item) for item in data]
        except Exception as exc:
            logger.warning("Failed to load patterns: %s", exc)
    
    def _save_patterns(self) -> None:
        """Save win/loss patterns to file."""
        try:
            with open(self._patterns_file, "w", encoding="utf-8") as f:
                json.dump([asdict(p) for p in self._patterns], f, indent=2)
        except Exception as exc:
            logger.error("Failed to save patterns: %s", exc)
    
    def update_edge_registry(
        self,
        market_id: str,
        edge_metrics: EdgeMetrics,
    ) -> None:
        """Update edge registry for a market."""
        self._edge_registry[market_id] = edge_metrics
        self._save_edge_registry()
        logger.info(
            "Edge registry updated: market=%s edge=%.2f%% confidence=%.2f liquidity=%.2f",
            market_id,
            edge_metrics.edge_percent * 100,
            edge_metrics.confidence_score,
            edge_metrics.liquidity_score,
        )
    
    def add_trade_pattern(
        self,
        edge_metrics: EdgeMetrics,
        outcome: str,
        pnl: float,
        market_category: str,
    ) -> None:
        """Add a trade pattern for learning."""
        pattern = TradePattern(
            edge_percent=edge_metrics.edge_percent,
            confidence_score=edge_metrics.confidence_score,
            liquidity_score=edge_metrics.liquidity_score,
            outcome=outcome,
            pnl=pnl,
            timestamp=time.time(),
            market_category=market_category,
        )
        self._patterns.append(pattern)
        self._save_patterns()
        logger.info(
            "Trade pattern added: outcome=%s pnl=%.2f edge=%.2f%%",
            outcome,
            pnl,
            edge_metrics.edge_percent * 100,
        )
    
    def get_win_rate_by_edge_range(
        self,
        min_edge: float,
        max_edge: float,
    ) -> float:
        """Calculate win rate for trades within edge range."""
        relevant = [
            p
            for p in self._patterns
            if min_edge <= p.edge_percent <= max_edge
        ]
        if not relevant:
            return 0.0
        wins = sum(1 for p in relevant if p.outcome == "win")
        return wins / len(relevant)
    
    def get_best_edge(self) -> Optional[EdgeMetrics]:
        """Get the best edge from registry."""
        if not self._edge_registry:
            return None
        return max(self._edge_registry.values(), key=lambda e: e.edge_percent)
    
    def get_win_patterns(self) -> List[TradePattern]:
        """Get winning patterns."""
        return [p for p in self._patterns if p.outcome == "win"]
    
    def get_loss_patterns(self) -> List[TradePattern]:
        """Get losing patterns."""
        return [p for p in self._patterns if p.outcome == "loss"]
    
    def get_avg_edge_by_outcome(self, outcome: str) -> float:
        """Get average edge by outcome."""
        relevant = [p for p in self._patterns if p.outcome == outcome]
        if not relevant:
            return 0.0
        return sum(p.edge_percent for p in relevant) / len(relevant)


# Global edge learning system instance
_edge_learning_system = EdgeLearningSystem()


def update_edge_registry(
    market_id: str,
    edge_metrics: EdgeMetrics,
) -> None:
    """Update edge registry using global system."""
    _edge_learning_system.update_edge_registry(market_id, edge_metrics)


def add_trade_pattern(
    edge_metrics: EdgeMetrics,
    outcome: str,
    pnl: float,
    market_category: str,
) -> None:
    """Add trade pattern using global system."""
    _edge_learning_system.add_trade_pattern(edge_metrics, outcome, pnl, market_category)


def get_win_rate_by_edge_range(min_edge: float, max_edge: float) -> float:
    """Get win rate by edge range using global system."""
    return _edge_learning_system.get_win_rate_by_edge_range(min_edge, max_edge)


def get_best_edge() -> Optional[EdgeMetrics]:
    """Get best edge using global system."""
    return _edge_learning_system.get_best_edge()


def get_win_patterns() -> List[TradePattern]:
    """Get win patterns using global system."""
    return _edge_learning_system.get_win_patterns()


def get_loss_patterns() -> List[TradePattern]:
    """Get loss patterns using global system."""
    return _edge_learning_system.get_loss_patterns()
