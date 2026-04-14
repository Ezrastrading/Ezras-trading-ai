"""Entry: python -m trading_ai.shark.run_shark — 24/7 daemon (requires apscheduler)."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

from pathlib import Path

runtime = (os.getenv("EZRAS_RUNTIME_ROOT") or "").strip()
if not runtime:
    runtime = "/app/ezras-runtime" if os.path.exists("/app") else str(Path.home() / "ezras-runtime")
Path(runtime).mkdir(parents=True, exist_ok=True)
for subdir in [
    "shark/state",
    "shark/logs",
    "shark/state/backups",
]:
    Path(runtime, subdir).mkdir(parents=True, exist_ok=True)

from trading_ai.shark.required_env import require_ezras_runtime_root

require_ezras_runtime_root()

from trading_ai.shark.reporting import startup_banner
from trading_ai.shark.scan_execute import run_gap_confirmed_hook, run_scan_execution_cycle
from trading_ai.shark.state_store import (
    backup_all_state_files,
    integrity_check_or_restore,
    load_bayesian_into_memory,
    load_capital,
    load_gaps,
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _setup_logging()
    log = logging.getLogger("shark.run")
    try:
        import resource

        # Optional hard cap — default OFF. A fixed 512MB cap caused ENOMEM during concurrent
        # HTTPS (SSL) on small Railway dynos. Set SHARK_RLIMIT_MB explicitly if you need a limit.
        raw_mb = (os.environ.get("SHARK_RLIMIT_MB") or "").strip()
        if raw_mb.isdigit():
            mb = int(raw_mb)
            if mb > 0:
                _lim = mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (_lim, _lim))
                log.info("Memory limit set: %sMB (RLIMIT_AS)", mb)
    except (ValueError, OSError) as exc:
        log.warning("Memory limit not applied: %s", exc)
    boot_unix = time.time()

    try:
        from trading_ai.shark.remote_state import restore_state_from_supabase

        n = restore_state_from_supabase()
        if n:
            log.info("Restored %s state file(s) from Supabase", n)
    except Exception as exc:
        log.warning("Supabase restore skipped: %s", exc)

    integrity_check_or_restore()
    load_bayesian_into_memory()
    try:
        from trading_ai.shark import ceo_sessions

        ceo_sessions.load_ceo_overrides_into_memory()
    except Exception as exc:
        log.warning("CEO overrides load skipped (non-blocking): %s", exc)

    try:
        from trading_ai.shark.mana_sandbox import maybe_run_mana_loss_learning_on_startup

        loss_rep = maybe_run_mana_loss_learning_on_startup()
        log.info("Mana loss learning startup: %s", loss_rep)
    except Exception as exc:
        log.warning("Mana loss learning startup failed (non-blocking): %s", exc)

    try:
        from trading_ai.shark.trade_journal import maybe_run_journal_loss_learning_on_startup

        jloss = maybe_run_journal_loss_learning_on_startup()
        log.info("Journal loss learning startup: %s", jloss)
    except Exception as exc:
        log.warning("Journal loss learning startup failed (non-blocking): %s", exc)

    try:
        from trading_ai.shark.health_server import start_health_server

        hp = int(os.environ.get("PORT") or 8080)
        start_health_server(hp)
        log.info("Health server on 0.0.0.0:%s /health", hp)
    except Exception as exc:
        log.warning("Health server failed (non-blocking): %s", exc)

    try:
        from trading_ai.shark.recovery import run_startup_recovery

        rep = run_startup_recovery(boot_unix=boot_unix)
        log.info("Startup recovery: %s", rep)
    except Exception as exc:
        log.warning("Startup recovery failed (non-blocking): %s", exc)

    try:
        from trading_ai.shark.balance_sync import sync_all_platforms

        sync_all_platforms()
        log.info("Initial balance sync completed")
    except Exception as exc:
        log.warning("Initial balance sync failed (non-blocking): %s", exc)

    rec = load_capital()
    g = load_gaps()
    gaps_n = len(g.get("gaps_under_observation") or [])
    from trading_ai.shark.capital_phase import detect_phase

    ph = detect_phase(rec.current_capital)
    banner = startup_banner(capital=rec.current_capital, phase=ph.value, gaps_n=gaps_n)
    print(banner)
    try:
        from trading_ai.shark.reporting import send_telegram

        if send_telegram(banner):
            log.info("Telegram startup banner sent")
    except Exception as exc:
        log.warning("Telegram startup banner failed (non-blocking): %s", exc)

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

        fetchers = tuple(default_fetchers())
        n, att = run_scan_execution_cycle(fetchers, tag="standard_scan")
        log.info("standard_scan: markets=%s execution_attempts=%s", n, att)

    def hot_scan() -> None:
        from trading_ai.shark.outlets import default_fetchers

        fetchers = tuple(default_fetchers())
        n, att = run_scan_execution_cycle(fetchers, tag="hot")
        log.info("hot_scan: markets=%s execution_attempts=%s", n, att)

    def gap_passive() -> None:
        from trading_ai.shark.outlets import default_fetchers

        run_gap_confirmed_hook()
        fetchers = tuple(default_fetchers())
        n, att = run_scan_execution_cycle(fetchers, tag="gap_passive")
        log.info("gap_passive: markets=%s execution_attempts=%s", n, att)

    def gap_active() -> None:
        from trading_ai.shark.outlets import default_fetchers

        run_gap_confirmed_hook()
        fetchers = tuple(default_fetchers())
        n, att = run_scan_execution_cycle(fetchers, tag="gap_active")
        log.info("gap_active: markets=%s execution_attempts=%s", n, att)

    def crypto_scalp_scan() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.outlets.polymarket import PolymarketFetcher

        fetchers = (PolymarketFetcher(),)
        hunt_filter = {HuntType.CRYPTO_SCALP}
        n, att = run_scan_execution_cycle(fetchers, tag="crypto_scalp", hunt_types_filter=hunt_filter)
        log.info("crypto_scalp_scan: markets=%s execution_attempts=%s", n, att)

    _crypto_scalp_sched = (
        crypto_scalp_scan
        if (os.environ.get("CRYPTO_SCALP_SCAN_ENABLED") or "false").strip().lower() == "true"
        else None
    )

    def near_resolution_sweep() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.outlets.polymarket import PolymarketFetcher

        fetchers = (PolymarketFetcher(),)
        hunt_filter = {HuntType.NEAR_RESOLUTION}
        n, att = run_scan_execution_cycle(fetchers, tag="near_resolution_sweep", hunt_types_filter=hunt_filter)
        log.info("near_resolution_sweep: markets=%s execution_attempts=%s", n, att)

    def kalshi_near_resolution() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.outlets.kalshi import KalshiFetcher

        fetchers = (KalshiFetcher(),)
        hunt_filter = {
            HuntType.NEAR_RESOLUTION,
            HuntType.NEAR_RESOLUTION_HV,
            HuntType.PURE_ARBITRAGE,
        }
        n, att = run_scan_execution_cycle(fetchers, tag="kalshi_near_resolution", hunt_types_filter=hunt_filter)
        log.info("kalshi_near_resolution: markets=%s execution_attempts=%s", n, att)

    def live_sports_hv_scan() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.outlets.kalshi import KalshiLiveSportsFetcher

        fetchers = (KalshiLiveSportsFetcher(),)
        hunt_filter = {HuntType.NEAR_RESOLUTION_HV}
        n, att = run_scan_execution_cycle(fetchers, tag="live_sports_hv", hunt_types_filter=hunt_filter)
        log.info("live_sports_hv: markets=%s execution_attempts=%s", n, att)

    def arb_sweep() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.outlets import default_fetchers

        fetchers = tuple(default_fetchers())
        hunt_filter = {HuntType.PURE_ARBITRAGE}
        n, att = run_scan_execution_cycle(fetchers, tag="arb_sweep", hunt_types_filter=hunt_filter)
        log.info("arb_sweep: markets=%s execution_attempts=%s", n, att)

    def kalshi_full_scan() -> None:
        from trading_ai.shark.outlets import default_fetchers

        fetchers = tuple(default_fetchers())
        n, att = run_scan_execution_cycle(fetchers, tag="kalshi_full")
        log.info("kalshi_full: markets=%s execution_attempts=%s", n, att)

    def kalshi_hf_scan() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.scan_execute import execution_fetchers_kalshi_only

        fset = {
            HuntType.KALSHI_NEAR_CLOSE,
            HuntType.NEAR_RESOLUTION,
            HuntType.NEAR_RESOLUTION_HV,
            HuntType.PURE_ARBITRAGE,
            HuntType.KALSHI_MOMENTUM,
        }
        n, att = run_scan_execution_cycle(
            tuple(execution_fetchers_kalshi_only()),
            tag="kalshi_hf",
            hunt_types_filter=fset,
        )
        log.info("kalshi_hf: markets=%s execution_attempts=%s", n, att)

    def kalshi_convergence_scan() -> None:
        from trading_ai.shark.models import HuntType
        from trading_ai.shark.scan_execute import scan_fetchers_all

        n, att = run_scan_execution_cycle(
            tuple(scan_fetchers_all()),
            tag="kalshi_convergence",
            hunt_types_filter={
                HuntType.KALSHI_CONVERGENCE,
                HuntType.KALSHI_METACULUS_DIVERGE,
                HuntType.KALSHI_METACULUS_AGREE,
            },
        )
        log.info("kalshi_convergence: markets=%s execution_attempts=%s", n, att)

    def resolution_monitor() -> None:
        try:
            from trading_ai.shark.mana_sandbox import tick_mana_resolutions

            n = tick_mana_resolutions()
            if n:
                log.info("mana sandbox: resolved %s position(s)", n)
        except Exception as exc:
            log.warning("mana resolution monitor failed (non-blocking): %s", exc)

    def daily_memo() -> None:
        try:
            from trading_ai.shark.reporting import format_daily_summary, send_telegram, trading_capital_usd_for_alerts
            from trading_ai.shark.state import BAYES

            rec = load_capital()
            g = load_gaps()
            raw = g.get("gaps_under_observation")
            if isinstance(raw, list):
                gaps_monitored = [str(x) for x in raw]
            elif isinstance(raw, dict):
                gaps_monitored = list(raw.keys())
            else:
                gaps_monitored = []
            total = max(rec.total_trades, 1)
            wr = rec.winning_trades / total
            best_h = max(BAYES.hunt_weights, key=BAYES.hunt_weights.get) if BAYES.hunt_weights else "n/a"
            kalshi = trading_capital_usd_for_alerts(fallback=rec.current_capital)
            text = format_daily_summary(
                kalshi_usd=kalshi,
                win_rate=wr,
                best_hunt=str(best_h),
                trades_today=rec.total_trades,
                gaps_monitored=gaps_monitored,
            )
            send_telegram(text)
            log.info("daily memo Telegram sent")
        except Exception as exc:
            log.warning("daily memo failed (non-blocking): %s", exc)

    def weekly_summary() -> None:
        """Sunday: CEO week review + pipeline (non-blocking)."""
        try:
            from trading_ai.shark import ceo_sessions

            ceo_sessions.run_ceo_session_safe("WEEKLY_REVIEW")
            log.info("weekly CEO review completed")
        except Exception as exc:
            log.warning("weekly CEO review failed: %s", exc)

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

    def _heartbeat() -> None:
        try:
            from trading_ai.shark.reporting import send_shark_heartbeat_alert

            send_shark_heartbeat_alert(started_at=boot_unix)
        except Exception as exc:
            log.warning("heartbeat failed: %s", exc)

    def eod_force_scan() -> None:
        try:
            from trading_ai.shark.eod_force import run_end_of_day_force_trade
            from trading_ai.shark.outlets import default_fetchers

            n = run_end_of_day_force_trade(tuple(default_fetchers()))
            log.info("EOD_FORCE_TRADE: completed n=%s", n)
        except Exception as exc:
            log.warning("eod_force_scan failed: %s", exc)


    def ceo_brief(session_type: str) -> None:
        try:
            from trading_ai.shark import ceo_sessions

            ceo_sessions.run_ceo_session_safe(session_type)
            log.info("CEO session %s completed", session_type)
        except Exception as exc:
            log.warning("CEO session wrapper failed: %s", exc)

    def _daily_excel_report() -> None:
        try:
            from datetime import datetime

            from zoneinfo import ZoneInfo

            from trading_ai.shark.excel_reporter import generate_daily_excel
            from trading_ai.shark.reporting import send_excel_report
            from trading_ai.shark.trade_journal import get_summary_stats

            et = ZoneInfo("America/New_York")
            date_str = datetime.now(et).strftime("%Y-%m-%d")
            path = generate_daily_excel(date_str)
            stats = get_summary_stats(date_str)
            send_excel_report(path, date_str, stats)
            log.info("DAILY_EXCEL_REPORT: %s", path)
        except Exception as exc:
            log.warning("daily excel report failed: %s", exc)


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
        heartbeat=_heartbeat,
        eod_force_trade=eod_force_scan,
        crypto_scalp_scan=_crypto_scalp_sched,
        near_resolution_sweep=near_resolution_sweep,
        arb_sweep=arb_sweep,
        kalshi_near_resolution=kalshi_near_resolution,
        ceo_session=ceo_brief,
        daily_excel_report=_daily_excel_report,
        kalshi_hf_scan=kalshi_hf_scan,
        kalshi_convergence_scan=kalshi_convergence_scan,
        kalshi_full_scan=kalshi_full_scan,
        avenue_pulse=avenue_pulse,
        live_sports_scan=live_sports_hv_scan,
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
