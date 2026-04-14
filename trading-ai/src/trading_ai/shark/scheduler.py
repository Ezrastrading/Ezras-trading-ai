"""24/7 APScheduler jobs — scan, gap hunter, resolution monitor, backups, memos.

Scan callbacks (``standard_scan``, ``hot_scan``, ``gap_*``) are defined in
``run_shark.py`` and invoke ``scan_execute.run_scan_execution_cycle`` so hunts
score into ``run_execution_chain`` each cycle.

Optional ``eod_force_trade`` runs daily at 23:00 America/New_York (``eod_force.py``).
"""

from __future__ import annotations

import logging
import os
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
) -> Optional[Any]:
    if not _HAS_APS or BackgroundScheduler is None:
        logger.warning("apscheduler not installed; pip install apscheduler")
        return None
    tz = os.environ.get("SHARK_TZ", "UTC")
    sched = BackgroundScheduler(timezone=tz)

    sched.add_job(standard_scan, IntervalTrigger(minutes=5), id="scan_standard", replace_existing=True)
    if crypto_scalp_scan is not None:
        sched.add_job(crypto_scalp_scan, IntervalTrigger(seconds=30), id="crypto_scalp_scan", replace_existing=True)
    if near_resolution_sweep is not None:
        sched.add_job(near_resolution_sweep, IntervalTrigger(seconds=60), id="near_resolution_sweep", replace_existing=True)
    if arb_sweep is not None:
        sched.add_job(arb_sweep, IntervalTrigger(minutes=2), id="arb_sweep", replace_existing=True)

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
        sched.add_job(heartbeat, IntervalTrigger(hours=6), id="heartbeat", replace_existing=True)
    if eod_force_trade is not None and CronTrigger is not None:
        sched.add_job(
            eod_force_trade,
            CronTrigger(hour=23, minute=0, timezone="America/New_York"),
            id="eod_force_trade",
            replace_existing=True,
        )
    return sched
