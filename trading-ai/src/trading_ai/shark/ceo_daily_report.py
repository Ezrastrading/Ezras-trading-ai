"""CEO daily report: generate once per day with trading summary."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DailyReport:
    """Daily trading report for CEO session."""
    date: str  # YYYY-MM-DD
    total_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl_per_trade: float
    biggest_mistake: Optional[str]
    best_edge_found: Optional[str]
    timestamp: float


class CEODailyReportGenerator:
    """Generate daily CEO reports."""
    
    def __init__(self):
        self._memory_dir = Path(os.environ.get("EZRAS_RUNTIME_ROOT", "/app/ezras-runtime")) / "shark/ceo"
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._daily_report_file = self._memory_dir / "daily_report.json"
        self._last_report_date: Optional[str] = None
        self._load_last_report_date()
    
    def _load_last_report_date(self) -> None:
        """Load last report date."""
        try:
            if self._daily_report_file.exists():
                with open(self._daily_report_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._last_report_date = data.get("date")
        except Exception as exc:
            logger.warning("Failed to load last report date: %s", exc)
    
    def _save_daily_report(self, report: DailyReport) -> None:
        """Save daily report."""
        try:
            with open(self._daily_report_file, "w", encoding="utf-8") as f:
                json.dump(asdict(report), f, indent=2)
            logger.info("Daily CEO report saved: %s", report.date)
        except Exception as exc:
            logger.error("Failed to save daily report: %s", exc)
    
    def _get_current_date(self) -> str:
        """Get current date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def _should_generate_report(self) -> bool:
        """Check if report should be generated (once per day)."""
        current_date = self._get_current_date()
        return self._last_report_date != current_date
    
    def generate_report(
        self,
        total_trades: int,
        win_rate: float,
        total_pnl: float,
        avg_pnl_per_trade: float,
        biggest_mistake: Optional[str] = None,
        best_edge_found: Optional[str] = None,
    ) -> Optional[DailyReport]:
        """Generate daily report if not already generated today."""
        if not self._should_generate_report():
            logger.debug("Daily report already generated for today")
            return None
        
        current_date = self._get_current_date()
        report = DailyReport(
            date=current_date,
            total_trades=total_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl_per_trade=avg_pnl_per_trade,
            biggest_mistake=biggest_mistake,
            best_edge_found=best_edge_found,
            timestamp=time.time(),
        )
        
        self._save_daily_report(report)
        self._last_report_date = current_date
        
        # Log report summary
        logger.info(
            "CEO DAILY REPORT: date=%s trades=%d win_rate=%.2f%% total_pnl=%.2f avg_pnl=%.2f",
            report.date,
            report.total_trades,
            report.win_rate * 100,
            report.total_pnl,
            report.avg_pnl_per_trade,
        )
        
        return report
    
    def load_report(self) -> Optional[DailyReport]:
        """Load the most recent daily report."""
        try:
            if not self._daily_report_file.exists():
                return None
            with open(self._daily_report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return DailyReport(**data)
        except Exception as exc:
            logger.warning("Failed to load daily report: %s", exc)
            return None


# Global CEO daily report generator instance
_ceo_daily_report_generator = CEODailyReportGenerator()


def generate_ceo_daily_report(
    total_trades: int,
    win_rate: float,
    total_pnl: float,
    avg_pnl_per_trade: float,
    biggest_mistake: Optional[str] = None,
    best_edge_found: Optional[str] = None,
) -> Optional[DailyReport]:
    """Generate daily CEO report using global generator."""
    return _ceo_daily_report_generator.generate_report(
        total_trades,
        win_rate,
        total_pnl,
        avg_pnl_per_trade,
        biggest_mistake,
        best_edge_found,
    )


def load_ceo_daily_report() -> Optional[DailyReport]:
    """Load daily CEO report using global generator."""
    return _ceo_daily_report_generator.load_report()
