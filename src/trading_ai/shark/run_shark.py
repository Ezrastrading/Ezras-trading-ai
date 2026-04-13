"""Entry: python -m trading_ai.shark.run_shark — 24/7 daemon (requires apscheduler)."""

from __future__ import annotations

import logging
import signal
import sys
import time

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

from trading_ai.shark.required_env import require_ezras_runtime_root

require_ezras_runtime_root()

from trading_ai.shark.reporting import startup_banner
from trading_ai.shark.scan_execute import run_gap_confirmed_hook, run_scan_execution_cycle
from trading_ai.shark.state_store import backup_all_state_files, integrity_check_or_restore, load_capital, load_gaps


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _setup_logging()
    log = logging.getLogger("shark.run")
    integrity_check_or_restore()
    rec = load_capital()
    g = load_gaps()
    gaps_n = len(g.get("gaps_under_observation") or [])
    from trading_ai.shark.capital_phase import detect_phase

    ph = detect_phase(rec.current_capital)
    print(startup_banner(capital=rec.current_capital, phase=ph.value, gaps_n=gaps_n))

    try:
        from trading_ai.shark.reporting import send_setup_ping

        if send_setup_ping():
            log.info("Telegram setup ping queued")
    except Exception as exc:
        log.warning("Telegram startup test failed (non-blocking): %s", exc)

    try:
        from trading_ai.shark.scheduler import build_shark_scheduler
    except ImportError:
        build_shark_scheduler = None  # type: ignore

    if build_shark_scheduler is None:
        log.error("Scheduler module missing")
        sys.exit(1)

    from trading_ai.shark.state import HOT

    gap_state = {"active": False}

    def standard_scan() -> None:
        from trading_ai.shark.outlets import default_fetchers

        n, att = run_scan_execution_cycle(tuple(default_fetchers()), tag="standard")
        log.info("standard_scan: markets=%s execution_attempts=%s", n, att)

    def hot_scan() -> None:
        from trading_ai.shark.outlets import default_fetchers

        n, att = run_scan_execution_cycle(tuple(default_fetchers()), tag="hot")
        log.info("hot_scan: markets=%s execution_attempts=%s", n, att)

    def gap_passive() -> None:
        from trading_ai.shark.outlets import default_fetchers

        run_gap_confirmed_hook()
        n, att = run_scan_execution_cycle(tuple(default_fetchers()), tag="gap_passive")
        log.info("gap_passive: markets=%s execution_attempts=%s", n, att)

    def gap_active() -> None:
        from trading_ai.shark.outlets import default_fetchers

        run_gap_confirmed_hook()
        n, att = run_scan_execution_cycle(tuple(default_fetchers()), tag="gap_active")
        log.info("gap_active: markets=%s execution_attempts=%s", n, att)

    def resolution_monitor() -> None:
        pass

    def daily_memo() -> None:
        log.info("daily memo slot (wire Telegram)")

    def weekly_summary() -> None:
        log.info("weekly summary slot (wire Telegram)")

    def state_backup() -> None:
        backup_all_state_files()

    def health_check() -> None:
        log.info("health ok")

    def _balance_sync() -> None:
        try:
            from trading_ai.shark.balance_sync import sync_all_platforms
            from trading_ai.shark.growth_tracker import check_trajectory
            sync_all_platforms()
            check_trajectory()
        except Exception as exc:
            log.warning("balance sync error (non-blocking): %s", exc)

    sched = build_shark_scheduler(
        standard_scan=standard_scan,
        hot_scan=hot_scan,
        gap_passive_scan=gap_passive,
        gap_active_scan=gap_active,
        resolution_monitor=resolution_monitor,
        daily_memo=daily_memo,
        weekly_summary=weekly_summary,
        state_backup=state_backup,
        health_check=health_check,
        hot_window_active=lambda: HOT.is_hot(time.time()),
        gap_active=lambda: gap_state["active"],
        balance_sync=_balance_sync,
    )
    if sched is None:
        print("Install apscheduler: pip install apscheduler", file=sys.stderr)
        sys.exit(1)
    sched.start()
    log.info("Shark scheduler started — 24/7")

    def _stop(*_a: object) -> None:
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
