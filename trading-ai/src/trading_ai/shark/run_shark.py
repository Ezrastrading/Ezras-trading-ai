"""Entry: python -m trading_ai.shark.run_shark — 24/7 daemon (requires apscheduler)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import traceback

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
from trading_ai.governance.storage_architecture import shark_state_path
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
            sell_expired_positions_on_startup,
        )

        if coinbase_enabled():
            _coinbase_accumulator = CoinbaseAccumulator()
            n_fs = _coinbase_accumulator.force_sell_all_positions()
            if n_fs:
                log.info("Coinbase startup force-sell: sold %s position(s)", n_fs)
            n_ex = sell_expired_positions_on_startup()
            if n_ex:
                log.info("Coinbase startup: sold %s expired position(s)", n_ex)
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
    from trading_ai.shark.outlets import polymarket_enabled

    if not polymarket_enabled():
        log.info("Polymarket disabled (POLYMARKET_ENABLED=false) — Kalshi + Coinbase only; no Polymarket scans or sweeps")

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
        and polymarket_enabled()
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
            if (os.environ.get("KALSHI_PROFIT_EXIT_ENABLED") or "false").strip().lower() in (
                "1",
                "true",
                "yes",
            ):
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

    def _send_pre_session_briefing() -> None:
        """Send full CEO briefing Telegram sequence before first Kalshi session (9am ET open blitz)."""
        if (os.environ.get("CEO_PRE_SESSION_BRIEFING_ENABLED") or "true").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            return
        try:
            from trading_ai.shark.million_tracker import update_balance
            from trading_ai.shark.mission import generate_full_ceo_briefing
            from trading_ai.shark.reporting import send_telegram_safe
            from trading_ai.shark.state_store import load_capital
            from trading_ai.shark.supabase_logger import get_recent_trades
            from trading_ai.shark.trade_reports import get_combined_report

            cb_bal = 0.0
            try:
                from trading_ai.shark.outlets.coinbase import CoinbaseClient

                cb_bal = float(CoinbaseClient().get_usd_balance())
            except Exception:
                pass

            ka_bal = float(os.environ.get("KALSHI_ACTUAL_BALANCE", 0) or 0)
            total = cb_bal + ka_bal
            update_balance(cb_bal, ka_bal)

            today = get_combined_report("day")
            pnl = float(today["combined"]["total_pnl"])
            trades_n = int(today["combined"]["total_trades"])
            wr = float(today["coinbase"].get("win_rate", 0) or 0)

            start_ts = 1744761600  # Apr 16 2026 UTC
            day_num = max(1, int((time.time() - start_ts) / 86400) + 1)

            from trading_ai.shark.lessons import load_lessons

            lessons = load_lessons()
            book = load_capital()
            all_time = float(getattr(book, "total_pnl", 0) or 0)

            recent_trades = get_recent_trades(limit=50) or []

            messages = generate_full_ceo_briefing(
                total_balance=total,
                coinbase_bal=cb_bal,
                kalshi_bal=ka_bal,
                todays_pnl=pnl,
                todays_trades=trades_n,
                win_rate=wr,
                day_number=day_num,
                all_time_pnl=all_time,
                lessons=lessons.get("lessons", []),
                recent_trades=recent_trades,
            )

            for msg in messages:
                send_telegram_safe(msg)
                time.sleep(0.5)

            log.info("Pre-session briefing sent: %d messages", len(messages))
        except Exception as e:
            log.warning("Pre-session briefing: %s", e)

    def kalshi_stale_order_sweep() -> None:
        try:
            from trading_ai.shark.kalshi_stale_orders import run_kalshi_stale_resting_order_sweep

            run_kalshi_stale_resting_order_sweep()
        except Exception as exc:
            log.warning("kalshi stale order sweep failed: %s", exc)

    def kalshi_blitz() -> None:
        """Crypto blitz: 15-min cron Mon–Fri 9am–5pm ET + 120s backup; KXBTCD/KXBTC/KXETH/KXETHD."""
        if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            from trading_ai.shark.kalshi_blitz import run_kalshi_blitz

            run_kalshi_blitz()
        except Exception as exc:
            log.warning("kalshi_blitz failed (non-blocking): %s", exc)

    def crypto_market_open_blitz() -> None:
        """9:00:00 AM ET — if BTC/ETH series show volume, fire blitz for the first 15m window."""
        if (os.environ.get("KALSHI_BLITZ_ENABLED") or "true").strip().lower() not in ("1", "true", "yes"):
            return
        if (os.environ.get("KALSHI_CRYPTO_OPEN_BLITZ_ENABLED") or "true").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            return
        try:
            from trading_ai.shark.outlets.kalshi import KalshiClient

            client = KalshiClient()
            if not client.has_kalshi_credentials():
                return
            vol_btc = 0.0
            vol_eth = 0.0
            for ser in ("KXBTCD", "KXBTC"):
                j = client._request(
                    "GET",
                    "/markets",
                    params={"status": "open", "limit": 50, "series_ticker": ser},
                )
                for m in j.get("markets") or []:
                    if isinstance(m, dict):
                        vol_btc += float(m.get("volume_24h") or m.get("volume") or 0)
            for ser in ("KXETHD", "KXETH"):
                j = client._request(
                    "GET",
                    "/markets",
                    params={"status": "open", "limit": 50, "series_ticker": ser},
                )
                for m in j.get("markets") or []:
                    if isinstance(m, dict):
                        vol_eth += float(m.get("volume_24h") or m.get("volume") or 0)
            if vol_btc < 1.0 and vol_eth < 1.0:
                log.debug(
                    "crypto market open check: low volume (BTC vol=%.0f ETH vol=%.0f) — skip blitz",
                    vol_btc,
                    vol_eth,
                )
                return
            log.info(
                "MARKET OPEN DETECTED: BTC volume=$%.0f, ETH volume=$%.0f — blitz firing",
                vol_btc,
                vol_eth,
            )
            _send_pre_session_briefing()
            from trading_ai.shark.kalshi_blitz import run_kalshi_blitz

            run_kalshi_blitz()
        except Exception as exc:
            log.warning("crypto_market_open_blitz failed (non-blocking): %s", exc)

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
        if (os.environ.get("KALSHI_INDEX_BLITZ_ENABLED") or "false").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            from trading_ai.shark.kalshi_index_blitz import run_kalshi_index_blitz

            run_kalshi_index_blitz()
        except Exception as exc:
            log.warning("kalshi_index_blitz failed (non-blocking): %s", exc)

    def kalshi_simple_scan_job() -> None:
        try:
            from trading_ai.shark.kalshi_simple_scanner import run_simple_scan

            result = run_simple_scan()
            placed = result.get("placed", 0) if isinstance(result, dict) else result
            if placed:
                log.info("kalshi_simple_scan: placed=%s", placed)
        except Exception as exc:
            log.warning("kalshi_simple_scan failed: %s", exc)

    def kalshi_gate_c_job() -> None:
        try:
            from trading_ai.shark.kalshi_gate_c import run_gate_c

            result = run_gate_c()
            if isinstance(result, dict) and result.get("ok") and (
                result.get("exits") or result.get("placed")
            ):
                log.info("kalshi_gate_c: result=%s", result)
        except Exception as exc:
            log.warning("kalshi_gate_c failed: %s", exc)

    def kalshi_gate_a_job() -> None:
        try:
            from trading_ai.shark.kalshi_simple_scanner import run_gate_a_job_fetch

            result = run_gate_a_job_fetch()
            placed = result.get("placed", 0) if isinstance(result, dict) else 0
            if placed:
                log.info("kalshi_gate_a: placed=%s", placed)
        except Exception as exc:
            log.warning("kalshi_gate_a: %s", exc)

    def kalshi_gate_b_job() -> None:
        try:
            from trading_ai.shark.kalshi_gate_b import run_gate_b_job_fetch

            n = run_gate_b_job_fetch()
            if n:
                log.info("kalshi_gate_b: cycle=%s", n)
        except Exception as exc:
            log.warning("kalshi_gate_b: %s", exc)

    def kalshi_hv_gate_job() -> None:
        """High-value long-shot NO trades (index/crypto range far from spot). Hourly ET."""
        try:
            from trading_ai.shark.kalshi_high_value_gate import run_hv_scan
            from trading_ai.shark.outlets.kalshi import KalshiClient
            from trading_ai.shark.state_store import load_capital

            if (os.environ.get("KALSHI_HV_GATE_ENABLED") or "false").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return

            client = KalshiClient()
            book = load_capital()
            bal = float(book.current_capital or 0.0)
            n = run_hv_scan(client, bal)
            if n:
                log.info("kalshi_hv_gate: placed=%s", n)
        except Exception as exc:
            log.warning("kalshi_hv_gate failed: %s", exc)

    def kalshi_scalable_gate_job() -> None:
        """Single Kalshi strategy: scalable obvious-NO gate (see kalshi_scalable_gate)."""
        try:
            from datetime import datetime

            try:
                from zoneinfo import ZoneInfo

                now_et = datetime.now(ZoneInfo("America/New_York"))
            except Exception:
                from datetime import datetime as _dt, timedelta, timezone

                now_et = _dt.now(timezone(timedelta(hours=-5)))
            if not (9 <= now_et.hour <= 16):
                return
            if (os.environ.get("KALSHI_SCALABLE_ENABLED") or "").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            from trading_ai.shark.kalshi_scalable_gate import run_scalable_gate
            from trading_ai.shark.outlets.kalshi import KalshiClient
            from trading_ai.shark.state_store import load_capital

            client = KalshiClient()
            book = load_capital()
            balance = float(os.environ.get("KALSHI_ACTUAL_BALANCE", str(book.current_capital or 0)))
            n = run_scalable_gate(client, balance)
            if n:
                log.info("kalshi_scalable_gate: cycle=%s", n)
        except Exception as exc:
            log.warning("kalshi_scalable_gate: %s", exc)

    def kalshi_resolution_check_job() -> None:
        """Resolve open Kalshi scalable-gate positions (~60s)."""
        try:
            if (os.environ.get("KALSHI_SCALABLE_ENABLED") or "").strip().lower() not in (
                "1",
                "true",
                "yes",
            ):
                return
            from trading_ai.shark.kalshi_scalable_gate import check_resolutions
            from trading_ai.shark.outlets.kalshi import KalshiClient
            from trading_ai.shark.state_store import load_capital

            client = KalshiClient()
            book = load_capital()
            balance = float(os.environ.get("KALSHI_ACTUAL_BALANCE", str(book.current_capital or 0)))
            check_resolutions(client, balance)
        except Exception as exc:
            log.debug("kalshi_resolution_check: %s", exc)

    def coinbase_exit_check_job() -> None:
        try:
            acc = _coinbase_accumulator
            if acc is None:
                from trading_ai.shark.coinbase_accumulator import CoinbaseAccumulator

                acc = CoinbaseAccumulator()
            acc._run_exits_only()
        except Exception as exc:
            log.warning("coinbase_exit_check: %s", exc)

    def coinbase_profit_scan_job() -> None:
        try:
            acc = _coinbase_accumulator
            if acc is None:
                from trading_ai.shark.coinbase_accumulator import CoinbaseAccumulator

                acc = CoinbaseAccumulator()
            n = acc._run_profit_scan()
            if n:
                log.info("coinbase_profit_scan: exits=%s", n)
        except Exception as exc:
            log.warning("coinbase_profit_scan failed: %s", exc)

    def coinbase_loss_scan_job() -> None:
        try:
            acc = _coinbase_accumulator
            if acc is None:
                from trading_ai.shark.coinbase_accumulator import CoinbaseAccumulator

                acc = CoinbaseAccumulator()
            n = acc._run_loss_scan()
            if n:
                log.info("coinbase_loss_scan: exits=%s", n)
        except Exception as exc:
            log.warning("coinbase_loss_scan failed: %s", exc)

    def hourly_report() -> None:
        try:
            from datetime import datetime

            from trading_ai.shark.reporting import send_telegram_safe, trading_capital_usd_for_alerts
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
            try:
                from trading_ai.shark.supabase_logger import get_win_rate, log_performance

                cb_stats = get_win_rate("coinbase")
                coinbase_balance = 0.0
                if _coinbase_accumulator is not None:
                    try:
                        coinbase_balance = float(
                            _coinbase_accumulator._client.get_usd_balance()
                        )
                    except Exception:
                        coinbase_balance = 0.0
                log_performance(
                    platform="coinbase",
                    trades_count=cb_stats["total"],
                    wins=cb_stats["wins"],
                    losses=cb_stats["losses"],
                    profit_usd=cb_stats["pnl"],
                    balance_usd=coinbase_balance,
                )
            except Exception:
                pass

            try:
                from trading_ai.shark.lessons import load_lessons
                from trading_ai.shark.progression import generate_ceo_briefing, get_summary

                briefing = generate_ceo_briefing()
                msg = f"{msg}\n\n{briefing.strip()}"
                send_telegram_safe(msg)

                try:
                    from trading_ai.shark.supabase_logger import _get_client

                    client = _get_client()
                    if client:
                        summary = get_summary("today")
                        client.table("ceo_briefings").insert(
                            {
                                "briefing_text": briefing,
                                "total_trades": summary.get("trades", 0),
                                "total_pnl": summary.get("pnl_usd", 0),
                                "win_rate": summary.get("win_rate", 0),
                                "balance": summary.get("current_balance", 0),
                                "lessons_count": len(load_lessons().get("lessons", [])),
                            }
                        ).execute()
                except Exception:
                    pass
            except Exception:
                send_telegram_safe(msg)

            log.info("HOURLY_REPORT: sent")
        except Exception as exc:
            log.warning("hourly_report failed: %s", exc)

    def daily_briefing_job() -> None:
        try:
            from trading_ai.shark.lessons import load_lessons
            from trading_ai.shark.million_tracker import update_balance
            from trading_ai.shark.mission import generate_full_ceo_briefing
            from trading_ai.shark.reporting import send_telegram_safe
            from trading_ai.shark.state_store import load_capital
            from trading_ai.shark.supabase_logger import get_recent_trades
            from trading_ai.shark.trade_reports import get_combined_report

            cb_bal = 0.0
            try:
                from trading_ai.shark.outlets.coinbase import CoinbaseClient

                cb_bal = float(CoinbaseClient().get_usd_balance())
            except Exception:
                pass

            ka_bal = float(os.environ.get("KALSHI_ACTUAL_BALANCE", 0) or 0)
            total = cb_bal + ka_bal
            update_balance(cb_bal, ka_bal)

            today = get_combined_report("day")
            pnl = float(today["combined"]["total_pnl"])
            trades = int(today["combined"]["total_trades"])
            wr = float(today["coinbase"].get("win_rate", 0) or 0)

            book = load_capital()
            all_time = float(getattr(book, "total_pnl", 0) or 0)

            start_ts = 1744761600  # Apr 16 2026
            day_num = int((time.time() - start_ts) / 86400) + 1

            lessons_data = load_lessons()
            lessons = lessons_data.get("lessons", [])
            recent_trades = get_recent_trades(limit=50) or []

            messages = generate_full_ceo_briefing(
                total_balance=total,
                coinbase_bal=cb_bal,
                kalshi_bal=ka_bal,
                todays_pnl=pnl,
                todays_trades=trades,
                win_rate=wr,
                day_number=day_num,
                all_time_pnl=all_time,
                lessons=lessons,
                recent_trades=recent_trades,
            )

            for msg in messages:
                send_telegram_safe(msg)
                time.sleep(1)

            log.info("daily_briefing_job: Telegram sent (%s messages)", len(messages))
        except Exception as e:
            log.warning("CEO briefing failed: %s", e)

    def daily_full_report_job() -> None:
        try:
            from trading_ai.shark.million_tracker import get_daily_briefing
            from trading_ai.shark.mission import get_directives_summary
            from trading_ai.shark.reporting import send_telegram_safe
            from trading_ai.shark.trade_reports import format_report_for_telegram, get_combined_report

            send_telegram_safe(get_directives_summary())

            cb_bal = 0.0
            ka_bal = float(os.environ.get("KALSHI_ACTUAL_BALANCE", 0) or 0)
            try:
                from trading_ai.shark.outlets.coinbase import CoinbaseClient

                cb_bal = float(CoinbaseClient().get_usd_balance())
            except Exception:
                pass

            for period in ("day", "week", "month"):
                send_telegram_safe(format_report_for_telegram(period))

            today = get_combined_report("day")
            send_telegram_safe(
                get_daily_briefing(
                    cb_bal,
                    ka_bal,
                    today["combined"]["total_pnl"],
                    today["combined"]["total_trades"],
                    today["coinbase"].get("win_rate", 0),
                )
            )
            log.info("daily_full_report_job: Telegram sent")
        except Exception as e:
            log.warning("daily_full_report: %s", e)

    _coinbase_env_on = (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )

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
        near_resolution_sweep=near_resolution_sweep if polymarket_enabled() else None,
        arb_sweep=arb_sweep if polymarket_enabled() else None,
        kalshi_near_resolution=None,
        ceo_session=ceo_brief,
        daily_excel_report=_daily_excel_report,
        kalshi_hf_scan=None,
        kalshi_convergence_scan=None,
        kalshi_full_scan=None,
        avenue_pulse=avenue_pulse,
        live_sports_scan=None,
        kalshi_stale_order_sweep=None,
        kalshi_blitz=None,
        kalshi_sports_blitz=None,
        kalshi_non_crypto_hf=None,
        market_open_alert=None,
        market_close_alert=None,
        kalshi_blitz_cron=None,
        kalshi_index_blitz=None,
        hourly_report=hourly_report,
        daily_briefing=daily_briefing_job,
        daily_full_report=daily_full_report_job,
        coinbase_scan=_coinbase_accumulator.scan_and_trade if _coinbase_accumulator is not None else None,
        coinbase_exit_check=coinbase_exit_check_job
        if (_coinbase_accumulator is not None or _coinbase_env_on)
        else None,
        coinbase_profit_scan=coinbase_profit_scan_job
        if (_coinbase_accumulator is not None or _coinbase_env_on)
        else None,
        coinbase_loss_scan=coinbase_loss_scan_job
        if (_coinbase_accumulator is not None or _coinbase_env_on)
        else None,
        crypto_market_open_blitz=crypto_market_open_blitz,
        kalshi_simple_scan=None,
        kalshi_gate_c=None,
        kalshi_gate_a=kalshi_gate_a_job,
        kalshi_gate_b=kalshi_gate_b_job,
        kalshi_hv_gate=None,
        kalshi_scalable_gate=kalshi_scalable_gate_job,
        kalshi_scalable_resolution=kalshi_resolution_check_job,
    )
    if sched is None:
        print("Install apscheduler: pip install apscheduler", file=sys.stderr)
        sys.exit(1)
    sched.start()
    log.info("Shark scheduler started — 24/7")
    for job in sched.get_jobs():
        jid = job.id.lower()
        if any(
            x in jid
            for x in (
                "blitz",
                "crypto_15min",
                "simple_scan",
                "gate_c",
                "kalshi_gate_a",
                "kalshi_gate_b",
                "gate_a",
                "gate_b",
                "kalshi_hv_gate",
                "kalshi_scalable",
                "coinbase_scan",
                "coinbase_exit_check",
                "coinbase_profit_scan",
                "coinbase_loss_scan",
            )
        ):
            log.info("BLITZ JOB CONFIRMED: id=%s trigger=%s", job.id, job.trigger)

    def _send_bot_online_telegram() -> None:
        try:
            from datetime import datetime, timezone

            from trading_ai.shark.reporting import send_telegram, trading_capital_usd_for_alerts

            rec = load_capital()
            bal = trading_capital_usd_for_alerts(fallback=rec.current_capital)
            t = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            rp = shark_state_path("restart_count.json")
            restarts: dict = {"count": 0, "last_restart": 0.0}
            if rp.is_file():
                try:
                    raw = json.loads(rp.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        restarts.update(raw)
                except Exception:
                    pass
            restarts["count"] = int(restarts.get("count") or 0) + 1
            restarts["last_restart"] = time.time()
            try:
                rp.write_text(json.dumps(restarts, indent=2), encoding="utf-8")
            except Exception as wexc:
                log.warning("restart_count.json write failed: %s", wexc)
            n = int(restarts["count"])
            if n > 1:
                send_telegram(
                    f"⚠️ BOT RESTARTED (#{n}) — selling expired positions — ${bal:.2f}, {t}"
                )
                log.info("ONLINE: startup Telegram sent (RESTART #%s)", n)
            else:
                send_telegram(
                    f"✅ EZRAS BOT ONLINE — ${bal:.2f} ready, all systems active, {t}"
                )
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


def _safe_main() -> None:
    log = logging.getLogger("shark.run")
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    except SystemExit as e:
        code = e.code
        if code not in (0, None, False):
            try:
                from trading_ai.shark.reporting import send_telegram_fatal_once

                send_telegram_fatal_once(f"🛑 SHARK EXIT\nnon-zero exit: {code!r}")
            except Exception:
                pass
        raise
    except Exception as e:
        err = traceback.format_exc()
        log.critical("FATAL CRASH: %s\n%s", e, err)
        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(f"🚨 BOT CRASHED — restarting\n{str(e)[:200]}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    _safe_main()
