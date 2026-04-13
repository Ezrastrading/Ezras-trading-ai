from __future__ import annotations

import argparse
import logging
import sys

from trading_ai.automation.scheduler import run_scheduler_loop
from trading_ai.config import get_settings
from trading_ai.decisions.record import record_decision
from trading_ai.pipeline.run import run_pipeline
from trading_ai.storage.store import Store


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _setup_logging()
    # python -m trading_ai shark … → Ezras Shark CLI (see trading_ai.shark.cli)
    if len(sys.argv) >= 2 and sys.argv[1] == "shark":
        from trading_ai.shark.cli import main_shark

        sys.exit(main_shark(sys.argv[2:]))

    parser = argparse.ArgumentParser(prog="trading-ai", description="Prediction market AI partner (Phase 1)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run one pipeline cycle")
    p_run.add_argument("--dry-market-only", action="store_true", help="Fetch/filter markets only (no AI)")

    sub.add_parser("schedule", help="Run pipeline on an interval (see SCHEDULE_INTERVAL_MINUTES)")

    sub.add_parser("validate-env", help="Validate required environment variables")

    p_dec = sub.add_parser("record-decision", help="Log a human decision for a brief")
    p_dec.add_argument("--market-id", required=True)
    p_dec.add_argument("--brief-created-at", required=True, help="ISO timestamp matching the brief")
    p_dec.add_argument("--action", required=True, help="e.g. pass, trade, watch")
    p_dec.add_argument("--notes", default=None)

    args = parser.parse_args()
    settings = get_settings()

    if args.cmd == "validate-env":
        from trading_ai.validate_env import run_validation

        sys.exit(run_validation())

    if args.cmd == "run":
        if getattr(args, "dry_market_only", False):
            from trading_ai.clients.polymarket import fetch_markets, to_candidate
            from trading_ai.market.filters import filter_candidates

            raw = fetch_markets(settings)
            candidates = [to_candidate(m) for m in raw]
            filtered = filter_candidates(candidates, settings)
            print(f"candidates: {len(filtered)}")
            for c in filtered[:20]:
                print(c.model_dump())
            return

        run_id = run_pipeline(settings)
        print(f"run_id={run_id}")
        return

    if args.cmd == "schedule":
        run_scheduler_loop(settings)
        return

    if args.cmd == "record-decision":
        store = Store(settings.data_dir / "trading_ai.sqlite")
        record_decision(
            store,
            market_id=args.market_id,
            brief_created_at=args.brief_created_at,
            action=args.action,
            notes=args.notes,
        )
        print("decision recorded")
        return


if __name__ == "__main__":
    main()
