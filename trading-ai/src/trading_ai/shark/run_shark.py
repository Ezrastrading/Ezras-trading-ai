"""Entry: python -m trading_ai.shark.run_shark — 24/7 daemon (requires apscheduler)."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
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
    log.info("Kalshi order mode: %s", os.environ.get("KALSHI_HV_ORDER_MODE", "not set"))
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

    # ── Coinbase accumulator — init + startup position check ──────────────────
    _coinbase_accumulator = None
    try:
        from trading_ai.shark.coinbase_accumulator import (
            CoinbaseAccumulator,
            coinbase_enabled,
        )

        if coinbase_enabled():
            _coinbase_accumulator = CoinbaseAccumulator()
            _coinbase_accumulator.load_and_check_positions_on_startup()
            log.info("Coinbase accumulator initialised")
        else:
            log.info("Coinbase disabled (set COINBASE_ENABLED=true to activate)")
    except Exception as exc:
        log.warning("Coinbase accumulator init failed (non-blocking): %s", exc)

    rec = load_capital()
    g = load_gaps()
    gaps_n = len(g.get("gaps_under_observation") or [])
    from trading_ai.shark.capital_phase import detect_phase

    ph = detect_phase(rec.current_capital)
    banner = startup_banner(capital=rec.current_capital, phase=ph.value, gaps_n=gaps_n)
    print(banner)
    log.info("%s", banner)

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
            from trading_ai.shark.kalshi_profit_exit import run_kalshi_profit_exit_scan

            run_kalshi_profit_exit_scan()
        except Exception as exc:
            log.warning("kalshi profit exit scan failed (non-blocking): %s", exc)
        try:
            from trading_ai.shark.mana_sandbox import tick_mana_resolutions

            n = tick_mana_resolutions()
            if n:
                log.info("mana sandbox: resolved %s position(s)", n)
        except Exception as exc:
            log.warning("mana resolution monitor failed (non-blocking): %s", exc)
        # Poll Kalshi (and all other outlet) open positions for resolution every 60s.
        # reconcile_open_positions() calls handle_resolution() which fires the
        # TRADE CLOSED Telegram notification.  Without this block the notification
        # never fires in production because reconcile_open_positions() was previously
        # only called once at boot (run_startup_recovery).
        try:
            from trading_ai.shark.recovery import reconcile_open_positions

            stats = reconcile_open_positions()
            if stats.get("resolved", 0):
                log.info(
                    "kalshi resolution poller: resolved=%s checked=%s (TRADE CLOSED notification fired)",
                    stats["resolved"],
                    stats["checked"],
                )
            elif stats.get("checked", 0):
                log.debug(
                    "kalshi resolution poller: checked=%s open position(s), none resolved yet",
                    stats["checked"],
                )
        except Exception as exc:
            log.warning("kalshi resolution poller failed (non-blocking): %s", exc)

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

    from trading_ai.shark.avenue_activator import scan_and_alert_transitions

    def avenue_pulse() -> None:
        scan_and_alert_transitions()

    def kalshi_stale_order_sweep() -> None:
        try:
            from trading_ai.shark.kalshi_stale_orders import run_kalshi_stale_resting_order_sweep

            run_kalshi_stale_resting_order_sweep()
        except Exception as exc:
            log.warning("kalshi stale order sweep failed: %s", exc)

    def kalshi_blitz() -> None:
        """Crypto blitz: runs every 2 minutes with a rolling close window (15m + hourly BTC markets)."""
        if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            from trading_ai.shark.kalshi_blitz import run_kalshi_blitz

            run_kalshi_blitz()
        except Exception as exc:
            log.warning("kalshi_blitz failed (non-blocking): %s", exc)

    def kalshi_sports_blitz() -> None:
        if (os.environ.get("KALSHI_SPORTS_BLITZ_ENABLED") or "false").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            return
        try:
            from trading_ai.shark.kalshi_sports_blitz import run_kalshi_sports_blitz

            run_kalshi_sports_blitz()
        except Exception as exc:
            log.warning("kalshi_sports_blitz failed (non-blocking): %s", exc)

    def kalshi_non_crypto_hf() -> None:
        if (os.environ.get("KALSHI_NC_HF_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            from trading_ai.shark.kalshi_non_crypto_hf import run_kalshi_non_crypto_hf

            run_kalshi_non_crypto_hf()
        except Exception as exc:
            log.warning("kalshi non-crypto HF failed (non-blocking): %s", exc)

    # ── Schedule-awareness helpers ─────────────────────────────────────────────

    def is_crypto_market_hours() -> bool:
        """Legacy helper for hourly Telegram: US weekday 9–5 ET (Kalshi crypto runs 24/7)."""
        try:
            from datetime import datetime

            from zoneinfo import ZoneInfo

            now_et = datetime.now(ZoneInfo("America/New_York"))
            if now_et.weekday() >= 5:
                return False
            return 9 <= now_et.hour < 17
        except Exception:
            return True

    def kalshi_index_blitz() -> None:
        if (os.environ.get("KALSHI_INDEX_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            from trading_ai.shark.kalshi_index_blitz import run_kalshi_index_blitz

            run_kalshi_index_blitz()
        except Exception as exc:
            log.warning("kalshi_index_blitz failed (non-blocking): %s", exc)

    def hourly_report() -> None:
        try:
            from datetime import datetime

            from trading_ai.shark.reporting import send_telegram, trading_capital_usd_for_alerts
            from trading_ai.shark.trade_journal import get_summary_stats

            try:
                from zoneinfo import ZoneInfo

                now_et = datetime.now(ZoneInfo("America/New_York"))
            except Exception:
                now_et = datetime.utcnow()

            rec = load_capital()
            bal = trading_capital_usd_for_alerts(fallback=rec.current_capital)
            date_str = now_et.strftime("%Y-%m-%d")
            try:
                stats = get_summary_stats(date_str)
                trades_today = stats.get("total_trades", 0)
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)
                pnl = float(stats.get("pnl_usd", 0.0) or 0.0)
            except Exception:
                trades_today = rec.total_trades
                wins = rec.winning_trades
                losses = max(0, trades_today - wins)
                pnl = 0.0
            pnl_pct = (pnl / bal * 100) if bal > 0 else 0.0
            crypto_status = "OPEN" if is_crypto_market_hours() else "CLOSED"
            sports_enabled = (os.environ.get("KALSHI_SPORTS_BLITZ_ENABLED") or "false").strip().lower() in ("1", "true", "yes")
            sports_status = "ACTIVE" if sports_enabled else "QUIET"

            # ── Coinbase accumulator snapshot ─────────────────────────────────
            cb_section = ""
            try:
                if _coinbase_accumulator is not None:
                    cb = _coinbase_accumulator.get_summary()
                    cb_pnl = float(cb.get("daily_pnl_usd") or 0.0)
                    cb_cost = float(cb.get("total_cost_usd") or 0.0)
                    cb_open = int(cb.get("open_count") or 0)
                    cb_total = float(cb.get("total_realized_usd") or 0.0)
                    by_pid = cb.get("by_product") or {}
                    pid_line = " | ".join(
                        f"{k}: {v}" for k, v in sorted(by_pid.items())
                    ) or "none"
                    cb_section = (
                        f"\n\n📊 COINBASE:\n"
                        f"  BTC/ETH/SOL open: {pid_line}\n"
                        f"  Deployed: ${cb_cost:.2f} | Positions: {cb_open}\n"
                        f"  Today P&L: ${cb_pnl:+.2f} | All-time: ${cb_total:+.2f}"
                    )
            except Exception:
                pass

            msg = (
                f"⏰ EZRAS HOURLY:\n"
                f"💰 Balance: ${bal:.2f}\n"
                f"📊 Trades today: {trades_today}\n"
                f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
                f"🚀 P&L today: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                f"📈 Crypto: {crypto_status}\n"
                f"🏀 Sports: {sports_status}"
                f"{cb_section}"
            )
            send_telegram(msg)
            log.info("HOURLY_REPORT: sent")
        except Exception as exc:
            log.warning("hourly_report failed: %s", exc)

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
        kalshi_stale_order_sweep=kalshi_stale_order_sweep,
        kalshi_blitz=kalshi_blitz,
        kalshi_sports_blitz=kalshi_sports_blitz,
        kalshi_non_crypto_hf=kalshi_non_crypto_hf,
        market_open_alert=None,
        market_close_alert=None,
        kalshi_blitz_cron=kalshi_blitz,
        kalshi_index_blitz=kalshi_index_blitz,
        hourly_report=hourly_report,
        coinbase_scan=_coinbase_accumulator.scan_and_trade if _coinbase_accumulator is not None else None,
    )
    if sched is None:
        print("Install apscheduler: pip install apscheduler", file=sys.stderr)
        sys.exit(1)
    sched.start()
    log.info("Shark scheduler started — 24/7")
    for job in sched.get_jobs():
        if "blitz" in job.id.lower():
            log.info("BLITZ JOB CONFIRMED: id=%s trigger=%s", job.id, job.trigger)

    def _send_bot_online_telegram() -> None:
        try:
            from datetime import datetime, timezone

            from trading_ai.shark.reporting import send_telegram, trading_capital_usd_for_alerts

            rec = load_capital()
            bal = trading_capital_usd_for_alerts(fallback=rec.current_capital)
            t = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            send_telegram(f"✅ EZRAS BOT ONLINE — ${bal:.2f} ready, all systems active, {t}")
            log.info("ONLINE: startup Telegram sent (EZRAS BOT ONLINE)")
        except Exception as exc:
            log.warning("ONLINE Telegram failed: %s", exc)

    _send_bot_online_telegram()

    def _watchdog() -> None:
        while True:
            time.sleep(60)
            try:
                if not getattr(sched, "running", True):
                    log.critical("WATCHDOG: scheduler stopped — restarting")
                    try:
                        sched.start()
                        from datetime import datetime, timezone

                        from trading_ai.shark.reporting import send_telegram

                        t = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        send_telegram(f"🔄 EZRAS BOT RESTARTED — back online {t}")
                        log.info("WATCHDOG: scheduler restarted — Telegram sent (EZRAS BOT RESTARTED)")
                    except Exception as e:
                        log.critical("WATCHDOG: restart failed: %s", e)
                        os.kill(os.getpid(), signal.SIGTERM)
            except Exception as exc:
                log.warning("WATCHDOG loop error: %s", exc)

    threading.Thread(target=_watchdog, name="shark-watchdog", daemon=True).start()

    _idle_state = {"last_idle_alert": 0.0, "last_market_hint": 0.0}

    def _idle_and_market_hours_loop() -> None:
        while True:
            time.sleep(60)
            try:
                now = time.time()
                rec = load_capital()
                lt = rec.last_trade_unix
                ref = float(lt) if lt is not None and float(lt) > 0 else boot_unix
                if now - ref > 600.0 and now - _idle_state["last_idle_alert"] >= 600.0:
                    from trading_ai.shark.reporting import send_telegram

                    send_telegram("⚠️ EZRAS: No trades in 10min — checking markets")
                    _idle_state["last_idle_alert"] = now
                if now - _idle_state["last_market_hint"] >= 300.0:
                    log.info(
                        "Waiting for markets... Market hours: crypto opens 9am ET, sports active during games"
                    )
                    _idle_state["last_market_hint"] = now
            except Exception as exc:
                log.warning("idle/market-hours loop: %s", exc)

    threading.Thread(target=_idle_and_market_hours_loop, name="shark-idle-hint", daemon=True).start()

    def _stop(*_a: object) -> None:
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except SystemExit as e:
        code = e.code
        if code not in (0, None, False):
            try:
                from trading_ai.shark.reporting import send_telegram_fatal_once

                send_telegram_fatal_once(f"🛑 SHARK EXIT\nnon-zero exit: {code!r}")
            except Exception:
                pass
        raise
    except Exception as exc:
        try:
            from trading_ai.shark.reporting import send_telegram_fatal_once

            send_telegram_fatal_once(f"🛑 SHARK FATAL\n{type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise
