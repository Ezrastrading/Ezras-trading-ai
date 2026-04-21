"""
Autonomous operator loop: validation products → micro-validation → readiness → reports → daily snapshot.

Stops when ``system_execution_lock.json`` has ``system_locked: false`` (operator unlock).
Uses exponential backoff on transient failures; does not exit on a single cycle error unless fatal.
"""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def exponential_backoff_seconds(attempt: int, *, base: float = 5.0, cap: float = 600.0) -> float:
    """Jittered exponential backoff for retry delays."""
    exp = min(cap, base * (2 ** min(attempt, 10)))
    jitter = random.uniform(0.0, min(3.0, exp * 0.1))
    return exp + jitter


def _should_run_loop(runtime_root: Optional[Path] = None) -> bool:
    from trading_ai.control.system_execution_lock import load_system_execution_lock

    lock = load_system_execution_lock(runtime_root=runtime_root)
    return bool(lock.get("system_locked"))


def run_live_operator_loop(
    *,
    loop_interval_seconds: float = 3600.0,
    micro_n: int = 1,
    runtime_root: Optional[Path] = None,
    product_id: str = "BTC-USD",
    on_cycle: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """
    Infinite loop until ``system_locked`` is false in ``data/control/system_execution_lock.json``.

    Each cycle:
      - multi-avenue scaffolds + control bundle
      - validation-products (writes control artifacts when API works)
      - micro-validation streak (n=micro_n)
      - final readiness JSON + human final report
      - daily trading summary (txt+json)
      - data index refresh
      - optional go-live confirmation text
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        from trading_ai.multi_avenue.auto_scaffold import ensure_all_registered_scaffolds
        from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle

        ensure_all_registered_scaffolds(runtime_root=root)
        write_multi_avenue_control_bundle(runtime_root=root)
    except Exception as exc:
        logger.warning("multi_avenue bootstrap (non-fatal): %s", exc)

    attempt = 0
    while True:
        if not _should_run_loop(runtime_root=root):
            logger.info("system_locked=false — exiting live operator loop")
            return

        summary: Dict[str, Any] = {"cycle_started": True, "runtime_root": str(root)}
        try:
            from trading_ai.control.data_index import refresh_data_index
            from trading_ai.control.system_execution_lock import touch_last_validation_timestamp
            from trading_ai.deployment.final_readiness_report import write_final_readiness_report
            from trading_ai.deployment.live_micro_validation import run_live_micro_validation_streak
            from trading_ai.deployment.readiness_decision import compute_final_readiness
            from trading_ai.deployment.validation_products_runner import run_validation_products
            from trading_ai.reports.daily_trading_summary import (
                write_daily_trade_snapshot,
                write_system_go_live_confirmation,
            )

            summary["validation_products"] = run_validation_products(runtime_root=root)

            skip_mv = (os.environ.get("LIVE_LOOP_SKIP_MICRO_VALIDATION") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if skip_mv:
                summary["micro_validation"] = {"skipped": True, "reason": "LIVE_LOOP_SKIP_MICRO_VALIDATION"}
            else:
                mv = run_live_micro_validation_streak(
                    n=max(1, int(micro_n)),
                    runtime_root=root,
                    product_id=product_id,
                )
                summary["micro_validation"] = mv
                if mv.get("live_validation_streak_passed"):
                    touch_last_validation_timestamp(runtime_root=root)

            rd = compute_final_readiness(write_files=True, trade_id_probe=None)
            summary["readiness"] = {"ready_for_first_20": rd.get("ready_for_first_20")}
            write_final_readiness_report(write_file=True)
            write_daily_trade_snapshot(runtime_root=root)
            refresh_data_index(runtime_root=root)
            write_system_go_live_confirmation(runtime_root=root, readiness=rd)

            attempt = 0
            summary["ok"] = True
        except Exception as exc:
            attempt += 1
            logger.exception("live loop cycle failed: %s", exc)
            summary["ok"] = False
            summary["error"] = str(exc)
            delay = exponential_backoff_seconds(attempt)
            logger.info("retry after %.1fs (attempt %s)", delay, attempt)
            time.sleep(delay)
            continue

        if on_cycle:
            try:
                on_cycle(summary)
            except Exception as exc:
                logger.warning("on_cycle hook failed: %s", exc)

        time.sleep(max(1.0, float(loop_interval_seconds)))


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Autonomous live validation + readiness loop")
    p.add_argument(
        "--interval-sec",
        type=float,
        default=float((os.environ.get("LIVE_LOOP_INTERVAL_SECONDS") or "3600").strip() or "3600"),
        help="Sleep between successful cycles (default 3600)",
    )
    p.add_argument("--micro-n", type=int, default=1, help="Micro-validation round trips per cycle (default 1)")
    p.add_argument("--product-id", default="BTC-USD", help="Preferred Coinbase product for micro-validation")
    args = p.parse_args()
    run_live_operator_loop(
        loop_interval_seconds=args.interval_sec,
        micro_n=args.micro_n,
        product_id=args.product_id,
    )


if __name__ == "__main__":
    main()
