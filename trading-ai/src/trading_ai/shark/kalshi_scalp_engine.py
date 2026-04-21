"""
Kalshi short-hold scalp engine: scan → enter → manage → exit → rescan.

REST order books by default; plug a WebSocket-backed client later by swapping ``KalshiClient``
or subclassing the scanner/position manager feeds.

Demo / paper: set ``KALSHI_SCALP_PAPER_MODE=true`` (default) and optionally
``KALSHI_API_BASE=https://demo.elections.kalshi.com/trade-api/v2``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from trading_ai.shark.kalshi_scalp_config import KalshiScalpConfig
from trading_ai.shark.kalshi_scalp_notifier import KalshiScalpNotifier
from trading_ai.shark.kalshi_scalp_position_manager import (
    KalshiScalpMetrics,
    KalshiScalpPositionManager,
    ScalpTrade,
)
from trading_ai.shark.kalshi_scalp_scanner import KalshiScalpScanner, normalize_scan_report

logger = logging.getLogger(__name__)


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def session_allows_trading(cfg: KalshiScalpConfig) -> bool:
    if not cfg.session_restrict_et:
        return True
    d = _et_now()
    if cfg.session_et_weekdays_only and d.weekday() >= 5:
        return False
    mins = d.hour * 60 + d.minute
    start = cfg.session_et_start_hour * 60 + cfg.session_et_start_minute
    end = cfg.session_et_end_hour * 60 + cfg.session_et_end_minute
    return start <= mins < end


@dataclass
class _HourlyWindow:
    bucket: int
    attempts: int = 0
    completed: int = 0


class KalshiScalpEngine:
    """
    Coordinates scanner + position manager, hourly caps, daily loss, consecutive losses.

    Call :meth:`run_forever` for a blocking loop or :meth:`run_step` for one tick (tests / embedding).
    """

    def __init__(
        self,
        cfg: Optional[KalshiScalpConfig] = None,
        *,
        scanner: Optional[KalshiScalpScanner] = None,
        position_manager: Optional[KalshiScalpPositionManager] = None,
        notifier: Optional[KalshiScalpNotifier] = None,
    ) -> None:
        self.cfg = cfg or KalshiScalpConfig.from_env()
        self.metrics = KalshiScalpMetrics()
        self.notifier = notifier or KalshiScalpNotifier()
        self.scanner = scanner or KalshiScalpScanner(self.cfg)
        self.pm = position_manager or KalshiScalpPositionManager(
            self.cfg, notifier=self.notifier, metrics=self.metrics
        )
        self.active_trade: Optional[ScalpTrade] = None
        self._last_scan_mono: float = 0.0
        self._last_pos_mono: float = 0.0
        self._hourly = _HourlyWindow(bucket=-1)
        self._day_key: str = ""
        self._realized_day_usd: float = 0.0
        self._consecutive_losses: int = 0
        self._halt_until_day_roll: bool = False

    def _roll_day(self) -> None:
        d = _et_now().strftime("%Y-%m-%d")
        if d != self._day_key:
            self._day_key = d
            self._realized_day_usd = 0.0
            self._halt_until_day_roll = False

    def _hour_bucket(self) -> int:
        return int(time.time() // 3600)

    def _roll_hourly(self) -> None:
        b = self._hour_bucket()
        if b != self._hourly.bucket:
            self._hourly = _HourlyWindow(bucket=b, attempts=0, completed=0)

    def _can_attempt_entry(self) -> bool:
        self._roll_day()
        self._roll_hourly()
        if self._halt_until_day_roll:
            return False
        if self.cfg.daily_loss_limit_usd is not None:
            if self._realized_day_usd <= -abs(float(self.cfg.daily_loss_limit_usd)):
                return False
        if self._consecutive_losses >= self.cfg.max_consecutive_losses:
            return False
        if self._hourly.attempts >= self.cfg.max_trade_attempts_per_hour:
            return False
        if self._hourly.completed >= self.cfg.max_completed_trades_per_hour:
            return False
        return True

    def _on_trade_closed(self, tr: ScalpTrade) -> None:
        self._roll_hourly()
        self._hourly.completed += 1
        r = float(tr.realized_pnl_usd or 0.0)
        self._realized_day_usd += r
        if r < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        if self.cfg.daily_loss_limit_usd is not None and self._realized_day_usd <= -abs(
            float(self.cfg.daily_loss_limit_usd)
        ):
            self._halt_until_day_roll = True
            logger.warning(
                "Kalshi scalp: daily loss limit reached (realized_day=%.2f) — halting new entries until next ET day",
                self._realized_day_usd,
            )

    def _market_row(self, ticker: str) -> Dict[str, Any]:
        try:
            j = self.pm.client.get_market(ticker)
            inner = j.get("market") if isinstance(j.get("market"), dict) else j
            return dict(inner) if isinstance(inner, dict) else {}
        except Exception as exc:
            logger.debug("get_market failed %s: %s", ticker, exc)
            return {}

    def run_step(self) -> Dict[str, Any]:
        """One scheduler tick: position checks (fast) + scanner cycle (slower)."""
        now = time.monotonic()
        report: Dict[str, Any] = {
            "session_ok": session_allows_trading(self.cfg),
            "active_trade_state": self.active_trade.state if self.active_trade else None,
        }

        if self.active_trade and self.active_trade.state == "OPEN":
            if now - self._last_pos_mono >= self.cfg.position_check_interval_seconds:
                self._last_pos_mono = now
                row = self._market_row(self.active_trade.market_ticker)
                self.active_trade = self.pm.evaluate(self.active_trade, row)
                if self.active_trade.state == "CLOSED":
                    self._on_trade_closed(self.active_trade)
                    self.active_trade = None
                elif self.active_trade.state == "FAILED":
                    self.active_trade = None

        if now - self._last_scan_mono < self.cfg.scanner_interval_seconds:
            report["metrics"] = self.metrics.__dict__.copy()
            self.notifier.on_engine_cycle(report)
            return report

        self._last_scan_mono = now
        self.metrics.scans_performed += 1

        if not report["session_ok"]:
            self.metrics.entries_skipped += 1
            self.notifier.on_scan_report(
                {
                    "families_scanned": list(self.cfg.series_for_families()),
                    "setup_approved": False,
                    "skipped_reason": "outside_session_window",
                }
            )
            report["metrics"] = self.metrics.__dict__.copy()
            self.notifier.on_engine_cycle(report)
            return report

        if self.active_trade is not None:
            self.metrics.entries_skipped += 1
            self.notifier.on_scan_report(
                {
                    "families_scanned": list(self.cfg.series_for_families()),
                    "setup_approved": False,
                    "skipped_reason": "max_open_positions",
                }
            )
            report["metrics"] = self.metrics.__dict__.copy()
            self.notifier.on_engine_cycle(report)
            return report

        if not self._can_attempt_entry():
            self.metrics.entries_skipped += 1
            self.notifier.on_scan_report(
                {
                    "families_scanned": list(self.cfg.series_for_families()),
                    "setup_approved": False,
                    "skipped_reason": "hourly_cap_or_risk_halt",
                }
            )
            report["metrics"] = self.metrics.__dict__.copy()
            self.notifier.on_engine_cycle(report)
            return report

        setup, meta = self.scanner.scan_best_setup()
        self.metrics.candidates_found += meta.get("candidates_found", 0)
        families_line: List[str] = ["S&P", "BTC", "ETH"]

        if setup is None:
            self.metrics.entries_skipped += 1
            self.notifier.on_scan_report(
                normalize_scan_report(families_line, False, skipped_reason="no_valid_setup")
            )
            report["metrics"] = self.metrics.__dict__.copy()
            self.notifier.on_engine_cycle(report)
            return report

        self._hourly.attempts += 1
        tr = self.pm.create_trade_from_setup(setup)
        self.notifier.on_entry_submitted(
            {
                "trade_id": tr.trade_id,
                "market_ticker": tr.market_ticker,
                "family": tr.family,
                "side": tr.side,
                "target_usd": tr.profit_target_usd,
                "stop_usd": tr.stop_loss_usd,
                "soft_timeout_sec": tr.soft_timeout_sec,
                "hard_timeout_sec": tr.hard_timeout_sec,
                "size": tr.size_contracts,
            }
        )
        tr = self.pm.execute_entry(tr, setup)
        if tr.state == "OPEN":
            self.metrics.entries_approved += 1
            self.active_trade = tr
            self.notifier.on_scan_report(
                normalize_scan_report(families_line, True, setup=setup, skipped_reason=None)
            )
        else:
            self.metrics.entries_skipped += 1
            self.notifier.on_scan_report(
                normalize_scan_report(families_line, False, skipped_reason="entry_failed")
            )

        report["metrics"] = self.metrics.__dict__.copy()
        self.notifier.on_engine_cycle(report)
        return report

    def run_forever(self, *, stop_flag: Optional[Any] = None) -> None:
        """
        Blocking loop until ``stop_flag`` is truthy (e.g. ``threading.Event``) or process exit.
        """
        logger.info(
            "Kalshi scalp engine started (paper=%s execution=%s base=%s)",
            self.cfg.paper_mode,
            self.cfg.execution_enabled,
            self.pm.client.base_url,
        )
        while True:
            if stop_flag is not None:
                is_set = getattr(stop_flag, "is_set", None)
                if callable(is_set) and is_set():
                    break
            try:
                self.run_step()
            except Exception as exc:
                logger.exception("Kalshi scalp run_step error: %s", exc)
            time.sleep(min(0.5, self.cfg.position_check_interval_seconds * 0.5))
