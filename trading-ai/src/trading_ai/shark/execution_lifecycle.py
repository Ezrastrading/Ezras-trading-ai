"""Execution lifecycle tracking: SCAN → INTENT → BUY → HOLD → EXIT → REBUY"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ExecutionStage(Enum):
    """Execution lifecycle stages."""
    SCAN = "SCAN"
    INTENT = "INTENT"
    BUY = "BUY"
    HOLD = "HOLD"
    EXIT = "EXIT"
    REBUY = "REBUY"


class ExecutionLifecycleTracker:
    """Track execution lifecycle with deduped logging per stage per trade."""
    
    def __init__(self):
        # trade_id -> {stage -> timestamp}
        self._trade_stages: Dict[str, Dict[ExecutionStage, float]] = {}
        # trade_id -> current_stage
        self._current_stages: Dict[str, ExecutionStage] = {}
    
    def log_stage(
        self,
        trade_id: str,
        stage: ExecutionStage,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log execution stage once per lifecycle."""
        if not trade_id:
            logger.warning("Lifecycle stage log missing trade_id")
            return
        
        current = self._current_stages.get(trade_id)
        
        # Validate lifecycle progression
        if current and not self._is_valid_progression(current, stage):
            logger.warning(
                "Invalid lifecycle progression: %s → %s for trade %s",
                current.value,
                stage.value,
                trade_id,
            )
            return
        
        # Log stage once per lifecycle
        if trade_id not in self._trade_stages:
            self._trade_stages[trade_id] = {}
        
        if stage in self._trade_stages[trade_id]:
            logger.debug(
                "Lifecycle stage %s already logged for trade %s (skipping)",
                stage.value,
                trade_id,
            )
            return
        
        # Log the stage
        self._trade_stages[trade_id][stage] = time.time()
        self._current_stages[trade_id] = stage
        
        ctx_str = ""
        if context:
            ctx_str = " " + " ".join(f"{k}={v}" for k, v in context.items())
        
        logger.info(
            "LIFECYCLE: %s %s%s",
            stage.value,
            trade_id,
            ctx_str,
        )
    
    def _is_valid_progression(
        self,
        current: ExecutionStage,
        next_stage: ExecutionStage,
    ) -> bool:
        """Check if stage progression is valid."""
        valid_progressions = {
            ExecutionStage.SCAN: [ExecutionStage.INTENT],
            ExecutionStage.INTENT: [ExecutionStage.BUY],
            ExecutionStage.BUY: [ExecutionStage.HOLD, ExecutionStage.EXIT],
            ExecutionStage.HOLD: [ExecutionStage.EXIT, ExecutionStage.REBUY],
            ExecutionStage.EXIT: [ExecutionStage.REBUY],
            ExecutionStage.REBUY: [ExecutionStage.HOLD, ExecutionStage.EXIT],
        }
        return next_stage in valid_progressions.get(current, [])
    
    def reset_trade(self, trade_id: str) -> None:
        """Reset lifecycle for a trade (e.g., after rebuy)."""
        if trade_id in self._trade_stages:
            del self._trade_stages[trade_id]
        if trade_id in self._current_stages:
            del self._current_stages[trade_id]
    
    def get_current_stage(self, trade_id: str) -> Optional[ExecutionStage]:
        """Get current stage for a trade."""
        return self._current_stages.get(trade_id)


# Global lifecycle tracker instance
_lifecycle_tracker = ExecutionLifecycleTracker()


def log_execution_stage(
    trade_id: str,
    stage: ExecutionStage,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Log execution stage using global tracker."""
    _lifecycle_tracker.log_stage(trade_id, stage, context)


def reset_trade_lifecycle(trade_id: str) -> None:
    """Reset lifecycle for a trade."""
    _lifecycle_tracker.reset_trade(trade_id)


def get_current_execution_stage(trade_id: str) -> Optional[ExecutionStage]:
    """Get current execution stage for a trade."""
    return _lifecycle_tracker.get_current_stage(trade_id)
