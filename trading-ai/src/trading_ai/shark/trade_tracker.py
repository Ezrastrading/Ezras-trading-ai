"""Trade tracking: store complete trade data for every position."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Complete trade record for tracking and learning."""
    market_id: str
    entry_price: float
    exit_price: float
    size: float
    fees: float
    slippage: float
    pnl_gross: float
    pnl_net: float
    hold_time: float  # seconds
    outcome: str  # "win" or "loss"
    reason_for_entry: str
    reason_for_exit: str
    timestamp: float
    outlet: str
    side: str  # "yes" or "no"
    edge_percent: Optional[float] = None
    confidence_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    fill_rate: Optional[float] = None


class TradeTracker:
    """Track all trades with complete data to EZRAS_RUNTIME_ROOT/shark/memory/."""
    
    def __init__(self):
        self._memory_dir = Path(os.environ.get("EZRAS_RUNTIME_ROOT", "/app/ezras-runtime")) / "shark/memory"
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._trades_file = self._memory_dir / "trades.jsonl"
    
    def record_trade(self, trade: TradeRecord) -> None:
        """Record a trade to the trades file."""
        try:
            with open(self._trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(trade)) + "\n")
            logger.info(
                "Trade recorded: market=%s outcome=%s pnl_net=%.2f",
                trade.market_id,
                trade.outcome,
                trade.pnl_net,
            )
        except Exception as exc:
            logger.error("Failed to record trade: %s", exc)
    
    def load_trades(self, limit: Optional[int] = None) -> List[TradeRecord]:
        """Load trades from the trades file."""
        trades = []
        try:
            if not self._trades_file.exists():
                return trades
            
            with open(self._trades_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        trades.append(TradeRecord(**data))
                    except Exception:
                        continue
                    if limit and len(trades) >= limit:
                        break
        except Exception as exc:
            logger.error("Failed to load trades: %s", exc)
        
        # Sort by timestamp descending (newest first)
        trades.sort(key=lambda t: t.timestamp, reverse=True)
        return trades
    
    def get_win_rate(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate win rate from trades."""
        if trades is None:
            trades = self.load_trades()
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.outcome == "win")
        return wins / len(trades)
    
    def get_total_pnl(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate total net PnL from trades."""
        if trades is None:
            trades = self.load_trades()
        return sum(t.pnl_net for t in trades)
    
    def get_avg_pnl_per_trade(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate average PnL per trade."""
        if trades is None:
            trades = self.load_trades()
        if not trades:
            return 0.0
        return self.get_total_pnl(trades) / len(trades)


# Global trade tracker instance
_trade_tracker = TradeTracker()


def record_trade(trade: TradeRecord) -> None:
    """Record a trade using global tracker."""
    _trade_tracker.record_trade(trade)


def load_trades(limit: Optional[int] = None) -> List[TradeRecord]:
    """Load trades using global tracker."""
    return _trade_tracker.load_trades(limit)


def get_win_rate(trades: Optional[List[TradeRecord]] = None) -> float:
    """Get win rate using global tracker."""
    return _trade_tracker.get_win_rate(trades)


def get_total_pnl(trades: Optional[List[TradeRecord]] = None) -> float:
    """Get total PnL using global tracker."""
    return _trade_tracker.get_total_pnl(trades)


def get_avg_pnl_per_trade(trades: Optional[List[TradeRecord]] = None) -> float:
    """Get average PnL per trade using global tracker."""
    return _trade_tracker.get_avg_pnl_per_trade(trades)
