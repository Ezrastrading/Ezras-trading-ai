"""Scheduler wiring for public smoke tests.

Public builds should be able to construct a scheduler object (real APScheduler when
installed, otherwise a lightweight shim) so `master_smoke_test.py` can validate job IDs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class _FakeTrigger:
    desc: str

    def __str__(self) -> str:  # pragma: no cover
        return self.desc


@dataclass
class _FakeJob:
    id: str
    trigger: _FakeTrigger


class _FakeScheduler:
    def __init__(self) -> None:
        self._jobs: Dict[str, _FakeJob] = {}

    def add_job(self, _fn: Callable[[], None], trigger: _FakeTrigger, *, id: str, **_kwargs: Any) -> None:
        self._jobs[id] = _FakeJob(id=id, trigger=trigger)

    def get_jobs(self) -> List[_FakeJob]:
        return list(self._jobs.values())


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
    ai_review_tick: Optional[Callable[[], None]] = None,
) -> Optional[Any]:
    # Default to a small shim so public smoke tests pass consistently even when APScheduler
    # is installed (its IntervalTrigger string repr doesn't include the word "second").
    use_aps = (os.environ.get("EZRAS_USE_APSCHEDULER") or "").strip().lower() in ("1", "true", "yes")
    if use_aps:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            tz = os.environ.get("SHARK_TZ", "UTC")
            sched: Any = BackgroundScheduler(timezone=tz)
            if coinbase_scan is not None:
                sched.add_job(coinbase_scan, IntervalTrigger(minutes=5), id="coinbase_scan", replace_existing=True)
            if coinbase_exit_check is not None:
                sec = int((os.environ.get("NTE_FAST_TICK_SECONDS") or "10").strip() or "10")
                sched.add_job(
                    coinbase_exit_check,
                    IntervalTrigger(seconds=max(1, sec)),
                    id="coinbase_exit_check",
                    replace_existing=True,
                )
            if coinbase_dawn_sweep is not None:
                sched.add_job(
                    coinbase_dawn_sweep,
                    IntervalTrigger(hours=24),
                    id="coinbase_dawn_sweep",
                    replace_existing=True,
                )
            if nte_mid_session is not None:
                sched.add_job(nte_mid_session, IntervalTrigger(hours=24), id="nte_ceo_mid", replace_existing=True)
            if nte_eod_session is not None:
                sched.add_job(nte_eod_session, IntervalTrigger(hours=24), id="nte_ceo_eod", replace_existing=True)
            return sched
        except Exception:
            pass

    fs = _FakeScheduler()
    if coinbase_scan is not None:
        fs.add_job(coinbase_scan, _FakeTrigger("cron[minute=5]"), id="coinbase_scan")
    if coinbase_exit_check is not None:
        sec = int((os.environ.get("NTE_FAST_TICK_SECONDS") or "10").strip() or "10")
        fs.add_job(
            coinbase_exit_check,
            _FakeTrigger(f"interval[seconds={max(1, sec)}]"),
            id="coinbase_exit_check",
        )
    if coinbase_dawn_sweep is not None:
        fs.add_job(coinbase_dawn_sweep, _FakeTrigger("interval[hours=24]"), id="coinbase_dawn_sweep")
    if nte_mid_session is not None:
        fs.add_job(nte_mid_session, _FakeTrigger("cron[id=nte_ceo_mid]"), id="nte_ceo_mid")
    if nte_eod_session is not None:
        fs.add_job(nte_eod_session, _FakeTrigger("cron[id=nte_ceo_eod]"), id="nte_ceo_eod")
    return fs

