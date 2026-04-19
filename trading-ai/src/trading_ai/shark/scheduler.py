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


def _polymarket_jobs_enabled() -> bool:
    from trading_ai.shark.outlets import polymarket_enabled

    return polymarket_enabled()


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
    daily_briefing: Optional[Callable[[], None]] = None,
    daily_full_report: Optional[Callable[[], None]] = None,
    coinbase_scan: Optional[Callable[[], None]] = None,
    coinbase_exit_check: Optional[Callable[[], None]] = None,
    coinbase_dawn_sweep: Optional[Callable[[], None]] = None,
    nte_mid_session: Optional[Callable[[], None]] = None,
    nte_eod_session: Optional[Callable[[], None]] = None,
    crypto_market_open_blitz: Optional[Callable[[], None]] = None,
    kalshi_simple_scan: Optional[Callable[[], None]] = None,
    kalshi_gate_c: Optional[Callable[[], None]] = None,
    kalshi_gate_a: Optional[Callable[[], None]] = None,
    kalshi_gate_b: Optional[Callable[[], None]] = None,
    kalshi_hv_gate: Optional[Callable[[], None]] = None,
    kalshi_scalable_gate: Optional[Callable[[], None]] = None,
    kalshi_scalable_resolution: Optional[Callable[[], None]] = None,
) -> Optional[Any]:
    if not _HAS_APS or BackgroundScheduler is None:
        logger.warning("apscheduler not installed; pip install apscheduler")
        return None
    tz = os.environ.get("SHARK_TZ", "UTC")
    sched = BackgroundScheduler(timezone=tz)

    if not _polymarket_jobs_enabled():
        logger.info("Polymarket disabled — skipping Polymarket-only jobs (crypto_scalp_scan, near_resolution_sweep, arb_sweep)")

    if kalshi_full_scan is not None:
        sched.add_job(kalshi_full_scan, IntervalTrigger(minutes=5), id="kalshi_full", replace_existing=True)
    else:
        sched.add_job(standard_scan, IntervalTrigger(minutes=5), id="scan_standard", replace_existing=True)
    if _polymarket_jobs_enabled():
        if crypto_scalp_scan is not None:
            sched.add_job(
                crypto_scalp_scan,
                IntervalTrigger(seconds=30),
                id="crypto_scalp_scan",
                replace_existing=True,
            )
        if near_resolution_sweep is not None:
            sched.add_job(
                near_resolution_sweep,
                IntervalTrigger(seconds=60),
                id="near_resolution_sweep",
                replace_existing=True,
            )
        if arb_sweep is not None:
            sched.add_job(arb_sweep, IntervalTrigger(minutes=2), id="arb_sweep", replace_existing=True)
    if kalshi_near_resolution is not None:
        sched.add_job(
            kalshi_near_resolution,
            IntervalTrigger(seconds=30),
            id="kalshi_near_resolution",
            max_instances=1,
            replace_existing=True,
        )

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
        if (os.environ.get("KALSHI_HF_ENABLED") or "false").strip().lower() != "true":
            return
        if kalshi_hf_scan is not None:
            kalshi_hf_scan()

    if kalshi_hf_scan is not None:
        sched.add_job(
            _kalshi_hf_wrapper,
            IntervalTrigger(seconds=30),
            id="kalshi_hf",
            max_instances=1,
            replace_existing=True,
        )
    if kalshi_convergence_scan is not None:
        sched.add_job(kalshi_convergence_scan, IntervalTrigger(seconds=60), id="kalshi_convergence", replace_existing=True)

    def _hot_wrapper() -> None:
        if hot_window_active():
            hot_scan()

    sched.add_job(_hot_wrapper, IntervalTrigger(seconds=30), id="scan_hot", max_instances=1, replace_existing=True)
    sched.add_job(gap_passive_scan, IntervalTrigger(minutes=15), id="gap_passive", replace_existing=True)

    def _gap_active_wrapper() -> None:
        if gap_active():
            gap_active_scan()

    sched.add_job(
        _gap_active_wrapper,
        IntervalTrigger(seconds=30),
        id="gap_active",
        max_instances=1,
        replace_existing=True,
    )
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
            IntervalTrigger(seconds=30),
            id="kalshi_stale_orders",
            max_instances=1,
            replace_existing=True,
        )
    # ── Crypto blitz: every 15 min Mon–Fri 9:00–4:45 PM ET (:00/:15/:30/:45 + 30 s) ─
    # KXBTCD/KXBTC/KXETH/KXETHD 15-minute windows; TTR 60–360 s (90%+ near close).
    if kalshi_blitz is not None and CronTrigger is not None:
        sched.add_job(
            kalshi_blitz,
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
    if kalshi_blitz is not None and IntervalTrigger is not None:
        sched.add_job(
            kalshi_blitz,
            IntervalTrigger(seconds=30),
            id="kalshi_blitz_backup",
            max_instances=1,
            replace_existing=True,
        )
    if crypto_market_open_blitz is not None and CronTrigger is not None:
        sched.add_job(
            crypto_market_open_blitz,
            CronTrigger(
                day_of_week="mon-fri",
                hour=9,
                minute=0,
                second=0,
                timezone="America/New_York",
            ),
            id="crypto_market_open_blitz",
            replace_existing=True,
        )
    if kalshi_sports_blitz is not None:
        sched.add_job(
            kalshi_sports_blitz,
            IntervalTrigger(seconds=30),
            id="kalshi_sports_blitz",
            max_instances=1,
            replace_existing=True,
        )
    if kalshi_non_crypto_hf is not None:
        sched.add_job(
            kalshi_non_crypto_hf,
            IntervalTrigger(seconds=30),
            id="kalshi_nc_hf",
            max_instances=1,
            replace_existing=True,
        )

    # ── Optional stock-session alerts (disabled by default — crypto runs 24/7) ─
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

    # ── CEO briefing 4×/day (9am, 12pm, 3pm, 6pm ET) — $1M goal + milestones ─
    if daily_briefing is not None and CronTrigger is not None:
        sched.add_job(
            daily_briefing,
            CronTrigger(
                hour="9,12,15,18",
                minute=0,
                second=0,
                timezone="America/New_York",
            ),
            id="ceo_briefing_4x",
            replace_existing=True,
        )

    # ── Full trade reports + million-tracker briefing (8am ET, once daily) ────
    if daily_full_report is not None and CronTrigger is not None:
        sched.add_job(
            daily_full_report,
            CronTrigger(
                hour=8,
                minute=0,
                second=0,
                timezone="America/New_York",
            ),
            id="daily_full_report",
            replace_existing=True,
        )

    # ── Kalshi simple rapid cycle (BTC/ETH/S&P; exits first; optional)
    if kalshi_simple_scan is not None and IntervalTrigger is not None:
        def _kalshi_simple_wrapper() -> None:
            if (os.environ.get("KALSHI_SIMPLE_SCAN_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            kalshi_simple_scan()  # type: ignore[misc]

        sched.add_job(
            _kalshi_simple_wrapper,
            IntervalTrigger(seconds=30),
            id="kalshi_simple_scan",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_gate_c is not None and IntervalTrigger is not None:
        def _gate_c_wrapper() -> None:
            if (os.environ.get("KALSHI_GATE_C_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            kalshi_gate_c()  # type: ignore[misc]

        sched.add_job(
            _gate_c_wrapper,
            IntervalTrigger(seconds=30),
            id="kalshi_gate_c",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_gate_a is not None and IntervalTrigger is not None:
        def _gate_a_wrapper() -> None:
            if (os.environ.get("KALSHI_GATE_A_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            kalshi_gate_a()  # type: ignore[misc]

        sched.add_job(
            _gate_a_wrapper,
            IntervalTrigger(minutes=5),
            id="kalshi_gate_a",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_gate_b is not None and IntervalTrigger is not None:
        def _gate_b_wrapper() -> None:
            if (os.environ.get("KALSHI_GATE_B_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            kalshi_gate_b()  # type: ignore[misc]

        sched.add_job(
            _gate_b_wrapper,
            IntervalTrigger(seconds=60),
            id="kalshi_gate_b",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_scalable_gate is not None and IntervalTrigger is not None:
        sched.add_job(
            kalshi_scalable_gate,
            IntervalTrigger(seconds=300),
            id="kalshi_scalable_gate",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_scalable_resolution is not None and IntervalTrigger is not None:
        sched.add_job(
            kalshi_scalable_resolution,
            IntervalTrigger(seconds=60),
            id="kalshi_scalable_resolution",
            max_instances=1,
            replace_existing=True,
        )

    if kalshi_hv_gate is not None and CronTrigger is not None:
        def _hv_gate_wrapper() -> None:
            if (os.environ.get("KALSHI_HV_GATE_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            kalshi_hv_gate()  # type: ignore[misc]

        sched.add_job(
            _hv_gate_wrapper,
            CronTrigger(
                minute=0,
                hour="9,10,11,12,13,14,15,16",
                day_of_week="mon-fri",
                timezone="America/New_York",
            ),
            id="kalshi_hv_gate",
            max_instances=1,
            replace_existing=True,
        )

    # ── Coinbase NTE: 5m entries; fast tick (exits + pending limits); optional dawn noop ─
    if coinbase_scan is not None:
        def _coinbase_scan_wrapper() -> None:
            if (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() not in (
                "1", "true", "yes"
            ):
                return
            coinbase_scan()  # type: ignore[misc]

        sched.add_job(
            _coinbase_scan_wrapper,
            IntervalTrigger(minutes=5),
            id="coinbase_scan",
            max_instances=2,
            replace_existing=True,
        )
    if coinbase_exit_check is not None:
        def _coinbase_exit_wrapper() -> None:
            if (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() not in (
                "1", "true", "yes"
            ):
                return
            coinbase_exit_check()  # type: ignore[misc]

        _fast_raw = (os.environ.get("NTE_FAST_TICK_SECONDS") or "10").strip()
        try:
            _fast_sec = int(_fast_raw)
        except ValueError:
            _fast_sec = 10
        _fast_sec = max(1, min(_fast_sec, 120))
        sched.add_job(
            _coinbase_exit_wrapper,
            IntervalTrigger(seconds=_fast_sec),
            id="coinbase_exit_check",
            max_instances=1,
            replace_existing=True,
        )

    if coinbase_dawn_sweep is not None and CronTrigger is not None:
        def _coinbase_dawn_wrapper() -> None:
            if (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            coinbase_dawn_sweep()  # type: ignore[misc]

        sched.add_job(
            _coinbase_dawn_wrapper,
            CronTrigger(hour=8, minute=0, timezone="America/New_York"),
            id="coinbase_dawn_sweep",
            max_instances=1,
            replace_existing=True,
        )

    # ── NTE: twice-daily CEO (mid-session + end-of-day ET) ─────────────────────
    if nte_mid_session is not None and CronTrigger is not None:
        sched.add_job(
            nte_mid_session,
            CronTrigger(hour=12, minute=0, timezone="America/New_York"),
            id="nte_ceo_mid",
            replace_existing=True,
        )
    if nte_eod_session is not None and CronTrigger is not None:
        sched.add_job(
            nte_eod_session,
            CronTrigger(hour=17, minute=0, timezone="America/New_York"),
            id="nte_ceo_eod",
            replace_existing=True,
        )

    return sched
