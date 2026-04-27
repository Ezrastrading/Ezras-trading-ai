"""Safety layer: max positions, max per trade, hard stop on daily loss."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    """Safety configuration."""
    max_positions: int = 1  # Max 1 live position until 20 clean trades reviewed
    max_per_trade_pct: float = 0.10  # 10% of capital (reduced from 15%)
    daily_loss_cap_pct: float = 0.10  # 10% daily loss cap
    paper_mode: bool = True  # Start with paper/dry-run validation
    clean_trades_required: int = 20  # Number of clean trades before live mode


@dataclass
class SafetyState:
    """Safety state tracking."""
    daily_pnl: float
    daily_trades: int
    current_positions: int
    daily_start_capital: float
    last_reset_date: str
    clean_trades_reviewed: int = 0  # Number of clean trades reviewed
    paper_mode: bool = True


class SafetyLayer:
    """Safety layer for trading risk management."""
    
    def __init__(self, config: Optional[SafetyConfig] = None):
        self._config = config or SafetyConfig()
        self._memory_dir = Path(os.environ.get("EZRAS_RUNTIME_ROOT", "/app/ezras-runtime")) / "shark/safety"
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._memory_dir / "state.json"
        self._state = SafetyState(
            daily_pnl=0.0,
            daily_trades=0,
            current_positions=0,
            daily_start_capital=0.0,
            last_reset_date=self._get_current_date(),
            clean_trades_reviewed=0,
            paper_mode=self._config.paper_mode,
        )
        self._load_state()
        self._check_daily_reset()
    
    def _get_current_date(self) -> str:
        """Get current date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def _load_state(self) -> None:
        """Load safety state from file."""
        try:
            if self._state_file.exists():
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._state = SafetyState(**data)
        except Exception as exc:
            logger.warning("Failed to load safety state: %s", exc)
    
    def _save_state(self) -> None:
        """Save safety state to file."""
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self._state), f, indent=2)
        except Exception as exc:
            logger.error("Failed to save safety state: %s", exc)
    
    def _check_daily_reset(self) -> None:
        """Reset daily state if date changed."""
        current_date = self._get_current_date()
        if self._state.last_reset_date != current_date:
            logger.info("Daily safety state reset: %s", current_date)
            self._state.daily_pnl = 0.0
            self._state.daily_trades = 0
            self._state.daily_start_capital = 0.0
            self._state.last_reset_date = current_date
            self._save_state()
    
    def set_daily_start_capital(self, capital: float) -> None:
        """Set daily starting capital."""
        self._state.daily_start_capital = capital
        self._save_state()
    
    def update_position_count(self, count: int) -> None:
        """Update current position count."""
        self._state.current_positions = count
        self._save_state()
    
    def record_trade_pnl(self, pnl: float) -> None:
        """Record trade PnL."""
        self._state.daily_pnl += pnl
        self._state.daily_trades += 1
        self._save_state()
    
    def check_max_positions(self, proposed_count: int) -> bool:
        """Check if adding proposed_count positions exceeds max."""
        new_count = self._state.current_positions + proposed_count
        if new_count > self._config.max_positions:
            logger.warning(
                "Safety: max positions exceeded (current=%d, proposed=%d, max=%d)",
                self._state.current_positions,
                proposed_count,
                self._config.max_positions,
            )
            return False
        return True
    
    def check_max_per_trade(self, trade_amount: float, capital: float) -> bool:
        """Check if trade amount exceeds max per trade percentage."""
        max_amount = capital * self._config.max_per_trade_pct
        if trade_amount > max_amount:
            logger.warning(
                "Safety: max per trade exceeded (trade=%.2f, max=%.2f, capital=%.2f)",
                trade_amount,
                max_amount,
                capital,
            )
            return False
        return True
    
    def check_daily_loss_cap(self) -> bool:
        """Check if daily loss exceeds cap."""
        if self._state.daily_start_capital <= 0:
            return True  # Can't check without starting capital
        
        loss_pct = abs(self._state.daily_pnl) / self._state.daily_start_capital
        if loss_pct > self._config.daily_loss_cap_pct:
            logger.critical(
                "SAFETY HALT: Daily loss cap exceeded (loss=%.2f%%, cap=%.2f%%)",
                loss_pct * 100,
                self._config.daily_loss_cap_pct * 100,
            )
            return False
        return True
    
    def is_paper_mode(self) -> bool:
        """Check if system is in paper mode."""
        return self._state.paper_mode
    
    def increment_clean_trades_reviewed(self) -> None:
        """Increment clean trades reviewed count."""
        self._state.clean_trades_reviewed += 1
        self._save_state()
        
        # Check if we can exit paper mode
        if self._state.clean_trades_reviewed >= self._config.clean_trades_required:
            logger.info(
                "Clean trades threshold reached (%d), exiting paper mode",
                self._config.clean_trades_required,
            )
            self._state.paper_mode = False
            self._save_state()
    
    def can_enable_live_trading(self) -> bool:
        """Check if live trading can be enabled (paper mode disabled or threshold reached)."""
        if not self._state.paper_mode:
            return True
        if self._state.clean_trades_reviewed >= self._config.clean_trades_required:
            return True
        logger.info(
            "Paper mode active: %d/%d clean trades reviewed",
            self._state.clean_trades_reviewed,
            self._config.clean_trades_required,
        )
        return False
    
    def enable_live_trading(self) -> None:
        """Force enable live trading (manual override)."""
        self._state.paper_mode = False
        self._save_state()
        logger.warning("Live trading manually enabled (paper mode disabled)")
    
    def record_settlement_mismatch(self) -> None:
        """Record settlement mismatch - triggers safety halt."""
        logger.critical("SETTLEMENT MISMATCH DETECTED - SAFETY HALT TRIGGERED")
        # Force paper mode on settlement mismatch
        self._state.paper_mode = True
        self._save_state()
    
    def get_daily_pnl(self) -> float:
        """Get daily PnL."""
        return self._state.daily_pnl
    
    def get_daily_trades(self) -> int:
        """Get daily trade count."""
        return self._state.daily_trades
    
    def get_current_positions(self) -> int:
        """Get current position count."""
        return self._state.current_positions


# Global safety layer instance
_safety_layer = SafetyLayer()


def set_daily_start_capital(capital: float) -> None:
    """Set daily starting capital using global safety layer."""
    _safety_layer.set_daily_start_capital(capital)


def update_position_count(count: int) -> None:
    """Update position count using global safety layer."""
    _safety_layer.update_position_count(count)


def record_trade_pnl(pnl: float) -> None:
    """Record trade PnL using global safety layer."""
    _safety_layer.record_trade_pnl(pnl)


def check_max_positions(proposed_count: int) -> bool:
    """Check max positions using global safety layer."""
    return _safety_layer.check_max_positions(proposed_count)


def check_max_per_trade(trade_amount: float, capital: float) -> bool:
    """Check max per trade using global safety layer."""
    return _safety_layer.check_max_per_trade(trade_amount, capital)


def check_daily_loss_cap() -> bool:
    """Check daily loss cap using global safety layer."""
    return _safety_layer.check_daily_loss_cap()


def get_daily_pnl() -> float:
    """Get daily PnL using global safety layer."""
    return _safety_layer.get_daily_pnl()


def get_daily_trades() -> int:
    """Get daily trades using global safety layer."""
    return _safety_layer.get_daily_trades()


def get_current_positions() -> int:
    """Get current positions using global safety layer."""
    return _safety_layer.get_current_positions()


def is_paper_mode() -> bool:
    """Check if system is in paper mode using global safety layer."""
    return _safety_layer.is_paper_mode()


def increment_clean_trades_reviewed() -> None:
    """Increment clean trades reviewed using global safety layer."""
    _safety_layer.increment_clean_trades_reviewed()


def can_enable_live_trading() -> bool:
    """Check if live trading can be enabled using global safety layer."""
    return _safety_layer.can_enable_live_trading()


def enable_live_trading() -> None:
    """Force enable live trading using global safety layer."""
    _safety_layer.enable_live_trading()


def record_settlement_mismatch() -> None:
    """Record settlement mismatch using global safety layer."""
    _safety_layer.record_settlement_mismatch()
