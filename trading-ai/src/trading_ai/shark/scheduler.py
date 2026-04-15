"""24/7 APScheduler jobs — scan, gap hunter, resolution monitor, backups, memos.

Scan callbacks (``standard_scan``, ``hot_scan``, ``gap_*``) are defined in
``run_shark.py`` and invoke ``scan_execute.run_scan_execution_cycle`` so hunts
score into ``run_execution_chain`` each cycle.

Optional ``eod_force_trade`` runs daily at 23:00 America/New_York (``eod_force.py``).
"""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    _HAS_APS = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    IntervalTrigger = None  # type: ignore
    _HAS_APS = False


def build_shark_scheduler(
    *,
    standard_scan: Callable[[], None],
    hot_scan: Callable[[], None],
    gap_passive_scan: Callable[[], None],
    gap_active_scan: Callable[[], None],
    resolution_monitor: Callable[[], None],
    daily_memo: Callable[[], None],
    weekly_summary: Callable[[], None],
    state_backup: Callable[[], None],
    health_check: Callable[[], None],
    hot_window_active: Callable[[], bool],
    gap_active: Callable[[], bool],
    balance_sync: Optional[Callable[[], None]] = None,
    heartbeat: Optional[Callable[[], None]] = None,
    eod_force_trade: Optional[Callable[[], None]] = None,
    crypto_scalp_scan: Optional[Callable[[], None]] = None,
    near_resolution_sweep: Optional[Callable[[], None]] = None,
    arb_sweep: Optional[Callable[[], None]] = None,
    kalshi_near_resolution: Optional[Callable[[], None]] = None,
    ceo_session: Optional[Callable[[str], None]] = None,
    daily_excel_report: Optional[Callable[[], None]] = None,
    kalshi_hf_scan: Optional[Callable[[], None]] = None,
    kalshi_convergence_scan: Optional[Callable[[], None]] = None,
    kalshi_full_scan: Optional[Callable[[], None]] = None,
    avenue_pulse: Optional[Callable[[], None]] = None,
    live_sports_scan: Optional[Callable[[], None]] = None,
    kalshi_stale_order_sweep: Optional[Callable[[], None]] = None,
    kalshi_blitz: Optional[Callable[[], None]] = None,
    kalshi_sports_blitz: Optional[Callable[[], None]] = None,
    kalshi_non_crypto_hf: Optional[Callable[[], None]] = None,
    market_open_alert: Optional[Callable[[], None]] = None,
    market_close_alert: Optional[Callable[[], None]] = None,
    kalshi_blitz_cron: Optional[Callable[[], None]] = None,
    kalshi_index_blitz: Optional[Callable[[], None]] = None,
    hourly_report: Optional[Callable[[], None]] = None,
) -> Optional[Any]:
    if not _HAS_APS or BackgroundScheduler is None:
        logger.warning("apscheduler not installed; pip install apscheduler")
        return None
    tz = os.environ.get("SHARK_TZ", "UTC")
    sched = BackgroundScheduler(timezone=tz)

    if kalshi_full_scan is not None:
        sched.add_job(kalshi_full_scan, IntervalTrigger(minutes=5), id="kalshi_full", replace_existing=True)
    else:
        sched.add_job(standard_scan, IntervalTrigger(minutes=5), id="scan_standard", replace_existing=True)
    if crypto_scalp_scan is not None:
        sched.add_job(crypto_scalp_scan, IntervalTrigger(seconds=30), id="crypto_scalp_scan", replace_existing=True)
    if near_resolution_sweep is not None:
        sched.add_job(near_resolution_sweep, IntervalTrigger(seconds=60), id="near_resolution_sweep", replace_existing=True)
    if arb_sweep is not None:
        sched.add_job(arb_sweep, IntervalTrigger(minutes=2), id="arb_sweep", replace_existing=True)
    if kalshi_near_resolution is not None:
        sched.add_job(kalshi_near_resolution, IntervalTrigger(seconds=60), id="kalshi_near_resolution", replace_existing=True)

    def _live_sports_hv_wrapper() -> None:
        if live_sports_scan is None:
            return
        try:
            from datetime import datetime

            from zoneinfo import ZoneInfo

            h = datetime.now(ZoneInfo("America/New_York")).hour
        except Exception:
            from datetime import datetime

            h = datetime.utcnow().hour
        if not (10 <= h < 24):
            return
        live_sports_scan()

    if live_sports_scan is not None:
        sched.add_job(
            _live_sports_hv_wrapper,
            IntervalTrigger(seconds=60),
            id="live_sports_hv",
            replace_existing=True,
        )

    def _kalshi_hf_wrapper() -> None:
        if (os.environ.get("KALSHI_HF_ENABLED") or "true").strip().lower() != "true":
            return
        if kalshi_hf_scan is not None:
            kalshi_hf_scan()

    if kalshi_hf_scan is not None:
        sched.add_job(_kalshi_hf_wrapper, IntervalTrigger(seconds=30), id="kalshi_hf", replace_existing=True)
    if kalshi_convergence_scan is not None:
        sched.add_job(kalshi_convergence_scan, IntervalTrigger(seconds=60), id="kalshi_convergence", replace_existing=True)

    def _hot_wrapper() -> None:
        if hot_window_active():
            hot_scan()

    sched.add_job(_hot_wrapper, IntervalTrigger(seconds=90), id="scan_hot", replace_existing=True)
    sched.add_job(gap_passive_scan, IntervalTrigger(minutes=15), id="gap_passive", replace_existing=True)

    def _gap_active_wrapper() -> None:
        if gap_active():
            gap_active_scan()

    sched.add_job(_gap_active_wrapper, IntervalTrigger(seconds=30), id="gap_active", replace_existing=True)
    sched.add_job(resolution_monitor, IntervalTrigger(seconds=60), id="resolution", replace_existing=True)
    sched.add_job(daily_memo, CronTrigger(hour=8, minute=0), id="daily_memo", replace_existing=True)
    sched.add_job(weekly_summary, CronTrigger(day_of_week="sun", hour=21, minute=0), id="weekly", replace_existing=True)
    sched.add_job(state_backup, CronTrigger(hour=0, minute=0), id="backup", replace_existing=True)
    sched.add_job(health_check, IntervalTrigger(minutes=30), id="health", replace_existing=True)
    if balance_sync is not None:
        sched.add_job(balance_sync, IntervalTrigger(minutes=5), id="balance_sync", replace_existing=True)
    if heartbeat is not None:
        sched.add_job(heartbeat, IntervalTrigger(minutes=5), id="heartbeat", replace_existing=True)
    if eod_force_trade is not None and CronTrigger is not None:
        sched.add_job(
            eod_force_trade,
            CronTrigger(hour=23, minute=0, timezone="America/New_York"),
            id="eod_force_trade",
            replace_existing=True,
        )
    if ceo_session is not None and CronTrigger is not None:
        for hour, minute, label in (
            (8, 0, "MORNING"),
            (12, 0, "MIDDAY"),
            (17, 0, "AFTERNOON"),
            (22, 0, "EOD"),
        ):
            sched.add_job(
                partial(ceo_session, label),
                CronTrigger(hour=hour, minute=minute, timezone="America/New_York"),
                id=f"ceo_{label.lower()}",
                replace_existing=True,
            )
    if daily_excel_report is not None and CronTrigger is not None:
        sched.add_job(
            daily_excel_report,
            CronTrigger(hour=23, minute=59, timezone="America/New_York"),
            id="daily_excel_report",
            replace_existing=True,
        )
    if avenue_pulse is not None:
        sched.add_job(avenue_pulse, IntervalTrigger(hours=2), id="avenue_pulse", replace_existing=True)
    if kalshi_stale_order_sweep is not None:
        sched.add_job(
            kalshi_stale_order_sweep,
            IntervalTrigger(minutes=2),
            id="kalshi_stale_orders",
            replace_existing=True,
        )
    if kalshi_blitz is not None:
        # Every 2 min (~30 runs/h) — aligns with 15m BTC/ETH windows (:00/:15/:30/:45); e.g. :13/:28/:43/:58 catches next close.
        sched.add_job(
            kalshi_blitz,
            IntervalTrigger(seconds=120),
            id="kalshi_blitz",
            replace_existing=True,
        )
    if kalshi_sports_blitz is not None:
        sched.add_job(
            kalshi_sports_blitz,
            IntervalTrigger(seconds=60),
            id="kalshi_sports_blitz",
            replace_existing=True,
        )
    if kalshi_non_crypto_hf is not None:
        sched.add_job(
            kalshi_non_crypto_hf,
            IntervalTrigger(seconds=30),
            id="kalshi_nc_hf",
            replace_existing=True,
        )

    # ── Market-open / market-close alerts ──────────────────────────────────────
    if market_open_alert is not None and CronTrigger is not None:
        sched.add_job(
            market_open_alert,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=59, timezone="America/New_York"),
            id="market_open_alert",
            replace_existing=True,
        )
    if market_close_alert is not None and CronTrigger is not None:
        sched.add_job(
            market_close_alert,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=55, timezone="America/New_York"),
            id="market_close_alert",
            replace_existing=True,
        )

    # ── Cron-precise crypto blitz: 9am ET open blast + :00/:15/:30/:45 ─────────
    if kalshi_blitz_cron is not None and CronTrigger is not None:
        # Force-fire at 9:00 AM ET market open
        sched.add_job(
            kalshi_blitz_cron,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=0, second=0, timezone="America/New_York"),
            id="kalshi_blitz_market_open",
            replace_existing=True,
        )
        # Every 15 min during market hours (aligns with :00/:15/:30/:45 BTC/ETH closes)
        sched.add_job(
            kalshi_blitz_cron,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-16",
                minute="0,15,30,45",
                second=30,
                timezone="America/New_York",
            ),
            id="crypto_15min_cron",
            replace_existing=True,
        )

    # ── Index blitz: every 30 min during NYSE hours ────────────────────────────
    if kalshi_index_blitz is not None and CronTrigger is not None:
        sched.add_job(
            kalshi_index_blitz,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute="0,30",
                second=30,
                timezone="America/New_York",
            ),
            id="kalshi_index_blitz",
            replace_existing=True,
        )

    # ── Hourly status report ───────────────────────────────────────────────────
    if hourly_report is not None and CronTrigger is not None:
        sched.add_job(
            hourly_report,
            CronTrigger(minute=0),
            id="hourly_report",
            replace_existing=True,
        )

    return sched
