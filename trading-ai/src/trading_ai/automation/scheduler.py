from __future__ import annotations

import logging
import signal
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler

from trading_ai.config import Settings
from trading_ai.pipeline.run import run_pipeline

logger = logging.getLogger(__name__)


def run_scheduler_loop(settings: Settings) -> None:
    interval = settings.schedule_interval_minutes
    if interval is None:
        raise ValueError("schedule_interval_minutes must be set for scheduler mode")

    def job() -> None:
        try:
            run_pipeline(settings)
            try:
                from trading_ai.ops.automation_heartbeat import record_heartbeat

                record_heartbeat("pipeline_schedule", ok=True, note="interval tick")
            except Exception:
                logger.debug("pipeline heartbeat record failed", exc_info=True)
        except Exception:
            logger.exception("Scheduled pipeline failed")

    sched = BackgroundScheduler()
    sched.add_job(job, "interval", minutes=interval, id="pipeline", max_instances=1)
    sched.start()
    logger.info("Scheduler started: every %s minutes", interval)

    def _stop(*_args: object) -> None:
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sched.shutdown(wait=False)
