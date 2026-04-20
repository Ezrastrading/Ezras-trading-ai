from __future__ import annotations

import argparse
import json
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
    parser = argparse.ArgumentParser(prog="trading-ai", description="Prediction market AI partner (Phase 1)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run one pipeline cycle")
    p_run.add_argument("--dry-market-only", action="store_true", help="Fetch/filter markets only (no AI)")
    p_run.add_argument(
        "--trace",
        action="store_true",
        help="Print pipeline step trace to stderr (same as PIPELINE_TRACE=1)",
    )

    sub.add_parser("schedule", help="Run pipeline on an interval (see SCHEDULE_INTERVAL_MINUTES)")

    sub.add_parser("validate-env", help="Validate required environment variables")

    sub.add_parser("audit-env", help="List which API keys are SET vs MISSING (never prints secrets)")

    sub.add_parser("export-metrics", help="Write data/ezras_metrics.json rollup from trades + samples")

    p_serve = sub.add_parser(
        "serve-api",
        help="HTTP server: health, metrics, pipeline, Kalshi smoke/dry-order (see api/server.py)",
    )
    p_serve.add_argument(
        "--debug",
        action="store_true",
        help="On bind failure, print full traceback (default: concise message only)",
    )

    sub.add_parser(
        "api-status",
        help="Check if API_HOST:API_PORT responds; GET /healthz when reachable (JSON to stdout)",
    )
    sub.add_parser(
        "api-stop-hint",
        help="Print commands to find/stop a process on API_PORT (does not kill anything)",
    )

    p_dec = sub.add_parser("record-decision", help="Log a human decision for a brief")
    p_dec.add_argument("--market-id", required=True)
    p_dec.add_argument("--brief-created-at", required=True, help="ISO timestamp matching the brief")
    p_dec.add_argument("--action", required=True, help="e.g. pass, trade, watch")
    p_dec.add_argument("--notes", default=None)

    sub.add_parser("kalshi-smoke", help="Kalshi: verify credentials via authenticated API call")

    p_kdry = sub.add_parser(
        "kalshi-dry-order",
        help="Kalshi: print dry-run order preview (never submits from CLI)",
    )
    p_kdry.add_argument("--ticker", required=True, help="Kalshi market ticker")
    p_kdry.add_argument(
        "--side",
        required=True,
        choices=("yes", "no"),
        help="Contract side: yes or no",
    )

    p_cb = sub.add_parser(
        "claude-bridge",
        help="JSON bridge to local HTTP API (set TRADING_AI_API_BASE, default http://127.0.0.1:8788)",
    )
    cb_sub = p_cb.add_subparsers(dest="bridge_action", required=True)
    cb_sub.add_parser("health", help="GET /healthz")
    cb_sub.add_parser("kalshi-smoke", help="GET /kalshi/smoke")
    p_cbd = cb_sub.add_parser("kalshi-dry-order", help="POST /kalshi/dry-order")
    p_cbd.add_argument("--ticker", required=True)
    p_cbd.add_argument("--side", required=True, choices=("yes", "no"))
    p_cbp = cb_sub.add_parser("pipeline-run", help="POST /pipeline/run")
    p_cbp.add_argument(
        "--webhook-telegrams",
        action="store_true",
        help="Allow webhook BUY Telegram path when configured",
    )
    cb_sub.add_parser(
        "self-test",
        help="Run health + Kalshi smoke via API; one concise JSON summary (no pipeline)",
    )

    sub.add_parser(
        "mcp-bridge",
        help="MCP stdio server → local API (Python 3.10+; pip install mcp)",
    )

    sub.add_parser("gptr-smoke", help="Verify GPT Researcher venv + cli paths (JSON to stdout)")
    sub.add_parser(
        "n8n-check",
        help="n8n reachable + golden workflow validates (ok + workflow_* flags; exit 1 if not)",
    )
    sub.add_parser(
        "apprise-smoke",
        help="Apprise test (configured/test_sent/reason); exit 1 only if notify fails when configured",
    )
    sub.add_parser("grafana-check", help="Metrics HTTP + Grafana provisioning files (JSON)")
    sub.add_parser(
        "automation-smoke",
        help="One safe pipeline run (no webhooks), history + metrics (JSON)",
    )
    sub.add_parser("calibration-report", help="Paper-trade calibration summary (JSON + human lines)")

    p_truth = sub.add_parser(
        "truth",
        help="Anti-hallucination safety layer: truth contracts, evidence binding, output validation",
    )
    p_truth.add_argument(
        "truth_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, validate-truth-layer, validate-output, run-gap-check, …",
    )

    p_phase2 = sub.add_parser(
        "phase2",
        help="Phase 2 intelligence: trade bank, DQS, audits, simulation (no live execution)",
    )
    p_phase2.add_argument(
        "phase2_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: log-trade, run-audit, show-performance, validate-system, …",
    )

    p_phase3 = sub.add_parser(
        "phase3",
        help="Phase 3: paper proof, gate, sizing advisory, execution queue (no live orders)",
    )
    p_phase3.add_argument(
        "phase3_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: show-paper, show-gate, queue-trade, validate-execution-system, …",
    )

    p_phase4 = sub.add_parser(
        "phase4",
        help="Phase 4: compounding engine, governor, Sharpe, portfolio heat, reports (additive)",
    )
    p_phase4.add_argument(
        "phase4_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: phase4-status, start-week, show-governor, validate-phase4, …",
    )

    p_phase5 = sub.add_parser(
        "phase5",
        help="Phase 5: capital intelligence, strategy registry, allocation, promotion/demotion (advisory)",
    )
    p_phase5.add_argument(
        "phase5_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, validate-phase5, incubate-strategy, run-strategy-audit, …",
    )

    p_phase6 = sub.add_parser(
        "phase6",
        help="Phase 6: multi-bot hierarchy, governance, institutional memory (advisory)",
    )
    p_phase6.add_argument(
        "phase6_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: validate-phase6, status, show-bots, create-bot, run-bot-audit, …",
    )

    p_phase_extra = sub.add_parser(
        "phase-extra",
        help="Phase Extra Side: institutional expansion (autonomy, cross-market, evolution, fund)",
    )
    p_phase_extra.add_argument(
        "phase_extra_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, validate-phase-extra, phase-extra show-mandate, …",
    )

    p_phase8 = sub.add_parser(
        "phase8",
        help="Phase 8: live capital execution and control (gates, brokers, risk-first)",
    )
    p_phase8.add_argument(
        "phase8_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, validate-phase8, execute-trade, run-audit, kill-switch, …",
    )

    p_phase10 = sub.add_parser(
        "phase10",
        help="Phase 10: CIO / institutional command (mandate, capital direction, governance; backend only)",
    )
    p_phase10.add_argument(
        "phase10_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, philosophy, run-audit, weekly-report, mandate-show, regime-forecast",
    )

    p_inst = sub.add_parser(
        "institutional",
        help="Phase 8.5–9.5: OMS/EMS, portfolio execution intelligence, fund governance",
    )
    p_inst.add_argument(
        "institutional_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, activation-seed, activation-flow, final-readiness-audit, smoke-readiness, controlled-backend-test, validate-institutional, …",
    )

    p_bk = sub.add_parser(
        "bookkeeping",
        help="Bookkeeping: canonical ledger, aggregates, Excel mirror, audits",
    )
    p_bk.add_argument(
        "bookkeeping_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, show-trade, sync-full, run-audit, …",
    )

    p_mh = sub.add_parser(
        "memory-harness",
        help="Open memory harness: context, durable memory, export/import",
    )
    p_mh.add_argument(
        "memory_harness_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, export, import, run-audit, …",
    )

    p_bo = sub.add_parser(
        "business-ops",
        help="Institutional bookkeeping, Excel mirror, partner loop, improvement",
    )
    p_bo.add_argument(
        "business_ops_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, show-trade, run-partner-review, run-scheduler, …",
    )

    p_phase75 = sub.add_parser(
        "phase7_5",
        help="Phase 7.5: sovereign operator control layer (monitoring, alerts, intervention)",
    )
    p_phase75.add_argument(
        "phase7_5_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status, dashboard, monitor, alerts, show-config, …",
    )

    p_repo = sub.add_parser(
        "repo-readiness",
        help="Tests + phase validators + git/docs/gitignore summary (commit handoff; no push)",
    )
    p_repo.add_argument("--format", choices=("json", "json-lines"), default="json")
    p_repo.add_argument("--skip-tests", action="store_true")

    p_telegram = sub.add_parser(
        "telegram",
        help="Telegram: ping, simulate OPEN/CLOSED alerts (uses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)",
    )
    p_telegram.add_argument(
        "telegram_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: ping, simulate-placed, simulate-closed",
    )

    p_vault = sub.add_parser(
        "vault-cycle",
        help="Vault morning/evening summaries; optional --send (see system/scripts/run_*_cycle.py)",
    )
    p_vault.add_argument(
        "vault_cycle_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: morning [--send], evening [--send]",
    )

    p_post = sub.add_parser(
        "post-trade",
        help="Instant post-trade: placed/closed (Telegram + runtime logs; see post_trade_hub)",
    )
    p_post.add_argument(
        "post_trade_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: placed, closed, from-file, simulate-placed, simulate-closed",
    )

    p_sizing = sub.add_parser(
        "sizing",
        help="Position sizing vs account risk bucket (status, validate-sample, simulate; audit)",
    )
    p_sizing.add_argument(
        "sizing_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status | validate-sample | simulate --size N --bucket NORMAL|REDUCED|BLOCKED|UNKNOWN",
    )

    p_exec = sub.add_parser(
        "execution",
        help="Execution reconciliation (intent vs fills, fees, drift flags)",
    )
    p_exec.add_argument(
        "execution_argv",
        nargs=argparse.REMAINDER,
        help="reconcile-status | show-last | simulate-reconcile ... | record-close ...",
    )

    p_lock = sub.add_parser(
        "lockouts",
        help="Hard daily / weekly / execution lockouts",
    )
    p_lock.add_argument(
        "lockouts_argv",
        nargs=argparse.REMAINDER,
        help="status | simulate-daily-loss | simulate-weekly-drawdown | clear-daily | clear-weekly",
    )

    p_truth_sync_cmd = sub.add_parser(
        "truth-sync",
        help="Venue / broker truth reconciliation (mock adapter)",
    )
    p_truth_sync_cmd.add_argument(
        "truth_sync_argv",
        nargs=argparse.REMAINDER,
        help="status | run | simulate-drift",
    )

    p_memo = sub.add_parser(
        "memo",
        help="Daily decision memo (deterministic, local data)",
    )
    p_memo.add_argument(
        "memo_argv",
        nargs=argparse.REMAINDER,
        help="generate-daily | show-last",
    )

    p_exc = sub.add_parser(
        "exceptions",
        help="Exception dashboard data layer (backend for UI)",
    )
    p_exc.add_argument(
        "exceptions_argv",
        nargs=argparse.REMAINDER,
        help="status | show-open | mark-resolved --id ...",
    )

    p_gov = sub.add_parser(
        "governance",
        help="Parameter change governance audit trail",
    )
    p_gov.add_argument(
        "governance_argv",
        nargs=argparse.REMAINDER,
        help="show-params | show-recent | record-change ...",
    )

    p_operational = sub.add_parser(
        "operational",
        help="Operational readiness checks (final-gap-check; distinct from phase institutional CLI)",
    )
    p_operational.add_argument(
        "operational_argv",
        nargs=argparse.REMAINDER,
        help="final-gap-check",
    )

    p_consistency = sub.add_parser(
        "consistency",
        help="Doctrine alignment, baseline, temporal summary (governance)",
    )
    p_consistency.add_argument(
        "consistency_argv",
        nargs=argparse.REMAINDER,
        help="status | check-sample | temporal | activate-local-operator | show-operator-registry | register-operator | approve-doctrine | baseline | diff",
    )

    p_storage = sub.add_parser(
        "storage",
        help="Storage architecture map (local vs runtime vs external)",
    )
    p_storage.add_argument(
        "storage_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status",
    )

    p_automation_scope = sub.add_parser(
        "automation-scope",
        help="What runs on schedules vs manual CLI; MacBook off behavior",
    )
    p_automation_scope.add_argument(
        "automation_scope_argv",
        nargs=argparse.REMAINDER,
        help="Subcommand: status",
    )

    sub.add_parser(
        "integrity-check",
        help="Doctrine integrity + consistency paths (exit 2 if HALT)",
    )

    sub.add_parser(
        "connectivity-audit",
        help="Heuristic audit: orphan modules, CLI coverage gaps (not perfect static analysis)",
    )

    p_smoke = sub.add_parser(
        "smoke-system",
        help="Deterministic Phases 2–5 + Phase 6 prep bridge smoke (no live venue execution)",
    )
    p_smoke.add_argument("--live", action="store_true", help="Use real package data/ tree")
    p_smoke.add_argument("--no-persist", action="store_true")

    p_sys = sub.add_parser(
        "system-status",
        help="Single dashboard: phases 2–6 prep + repo + smoke + connectivity summary",
    )
    p_sys.add_argument("--skip-repo-tests", action="store_true")

    sub.add_parser(
        "phase6-prep-status",
        help="Phase 6 prep: incentive model bridge to Phase 5 readiness (no bot hierarchy)",
    )

    args = parser.parse_args()

    from trading_ai.runtime_checks.cli_ssl_policy import enforce_ssl_for_primary_cli_command

    enforce_ssl_for_primary_cli_command(getattr(args, "cmd", None))

    if args.cmd == "telegram":
        from trading_ai.automation.telegram_cli import main_telegram

        sys.exit(main_telegram(args.telegram_argv))

    if args.cmd == "vault-cycle":
        from trading_ai.automation.telegram_cli import main_vault_cycle

        sys.exit(main_vault_cycle(args.vault_cycle_argv))

    if args.cmd == "post-trade":
        from trading_ai.automation.post_trade_cli import main_post_trade

        sys.exit(main_post_trade(args.post_trade_argv))

    if args.cmd == "sizing":
        from trading_ai.automation.sizing_cli import main_sizing

        sys.exit(main_sizing(args.sizing_argv))

    if args.cmd == "execution":
        from trading_ai.institutional_cli import main_execution

        sys.exit(main_execution(args.execution_argv))

    if args.cmd == "lockouts":
        from trading_ai.institutional_cli import main_lockouts

        sys.exit(main_lockouts(args.lockouts_argv))

    if args.cmd == "truth-sync":
        from trading_ai.institutional_cli import main_truth_sync

        sys.exit(main_truth_sync(args.truth_sync_argv))

    if args.cmd == "memo":
        from trading_ai.institutional_cli import main_memo

        sys.exit(main_memo(args.memo_argv))

    if args.cmd == "exceptions":
        from trading_ai.institutional_cli import main_exceptions

        sys.exit(main_exceptions(args.exceptions_argv))

    if args.cmd == "governance":
        from trading_ai.institutional_cli import main_governance

        sys.exit(main_governance(args.governance_argv))

    if args.cmd == "operational":
        from trading_ai.institutional_cli import main_operational

        sys.exit(main_operational(args.operational_argv))

    if args.cmd == "consistency":
        from trading_ai.governance.system_cli import main_consistency

        sys.exit(main_consistency(args.consistency_argv))

    if args.cmd == "storage":
        from trading_ai.governance.system_cli import main_storage

        sys.exit(main_storage(args.storage_argv))

    if args.cmd == "automation-scope":
        from trading_ai.governance.system_cli import main_automation_scope

        sys.exit(main_automation_scope(args.automation_scope_argv))

    if args.cmd == "integrity-check":
        from trading_ai.governance.system_cli import main_integrity_check

        sys.exit(main_integrity_check())

    settings = get_settings()
    if getattr(args, "trace", False):
        settings = settings.model_copy(update={"pipeline_trace": True})

    if args.cmd == "gptr-smoke":
        from trading_ai.phase1_checks import print_json, run_gptr_smoke_cmd

        out = run_gptr_smoke_cmd(settings)
        print_json(out)
        sys.exit(0 if out.get("ok") else 1)

    if args.cmd == "n8n-check":
        from trading_ai.phase1_checks import print_json, run_n8n_check

        out = run_n8n_check(settings)
        print_json(out)
        sys.exit(0 if out.get("ok") else 1)

    if args.cmd == "apprise-smoke":
        from trading_ai.phase1_checks import print_json, run_apprise_smoke

        out = run_apprise_smoke(settings)
        print_json(out)
        if out.get("configured") and not out.get("ok"):
            sys.exit(1)
        sys.exit(0)

    if args.cmd == "grafana-check":
        from trading_ai.phase1_checks import print_json, run_grafana_check

        out = run_grafana_check(settings)
        print_json(out)
        sys.exit(0 if out.get("ok") else 1)

    if args.cmd == "automation-smoke":
        from trading_ai.phase1_checks import print_json, run_automation_smoke

        out = run_automation_smoke(settings)
        print_json(out)
        sys.exit(0 if out.get("ok") else 1)

    if args.cmd == "calibration-report":
        from trading_ai.phase1_checks import print_calibration_human, print_json, run_calibration_report

        summ = run_calibration_report(settings)
        print_json(summ)
        print_calibration_human(summ)
        return

    if args.cmd == "truth":
        from trading_ai.truth.cli import main_truth

        sys.exit(main_truth(args.truth_argv))

    if args.cmd == "phase2":
        from trading_ai.phase2.cli import main_phase2

        sys.exit(main_phase2(args.phase2_argv))

    if args.cmd == "phase3":
        from trading_ai.phase3.cli import main_phase3

        sys.exit(main_phase3(args.phase3_argv))

    if args.cmd == "phase4":
        from trading_ai.phase4.cli import main_phase4

        sys.exit(main_phase4(args.phase4_argv))

    if args.cmd == "phase5":
        from trading_ai.phase5.cli import main_phase5

        sys.exit(main_phase5(args.phase5_argv))

    if args.cmd == "phase6":
        from trading_ai.phase6.cli import main_phase6

        sys.exit(main_phase6(args.phase6_argv))

    if args.cmd == "phase-extra":
        from trading_ai.phase_extra.cli import main_phase_extra

        sys.exit(main_phase_extra(args.phase_extra_argv))

    if args.cmd == "phase8":
        from trading_ai.phase8.cli import main_phase8

        sys.exit(main_phase8(args.phase8_argv))

    if args.cmd == "phase10":
        from trading_ai.phase10.cli import main_phase10

        sys.exit(main_phase10(args.phase10_argv))

    if args.cmd == "institutional":
        from trading_ai.phase_institutional.cli import main_institutional

        sys.exit(main_institutional(args.institutional_argv))

    if args.cmd == "phase7_5":
        from trading_ai.phase7_5.cli import main_phase7_5

        sys.exit(main_phase7_5(args.phase7_5_argv))

    if args.cmd == "bookkeeping":
        from trading_ai.bookkeeping.shared.bookkeeping_cli import main_bookkeeping

        sys.exit(main_bookkeeping(args.bookkeeping_argv))

    if args.cmd == "memory-harness":
        from trading_ai.memory_harness.harness.harness_core import main_memory_harness

        sys.exit(main_memory_harness(args.memory_harness_argv))

    if args.cmd == "business-ops":
        from trading_ai.business_ops.business_ops_cli import main_business_ops

        sys.exit(main_business_ops(args.business_ops_argv))

    if args.cmd == "repo-readiness":
        from trading_ai.repo_readiness import main_repo_readiness

        extra = []
        if getattr(args, "skip_tests", False):
            extra.append("--skip-tests")
        fmt = getattr(args, "format", "json")
        extra.extend(["--format", fmt])
        sys.exit(main_repo_readiness(extra))

    if args.cmd == "connectivity-audit":
        from trading_ai.system_connectivity_audit import main_connectivity_audit

        sys.exit(main_connectivity_audit())

    if args.cmd == "smoke-system":
        from trading_ai.system_smoke import main_smoke_system

        sm: list = []
        if getattr(args, "live", False):
            sm.append("--live")
        if getattr(args, "no_persist", False):
            sm.append("--no-persist")
        sys.exit(main_smoke_system(sm))

    if args.cmd == "system-status":
        from trading_ai.system_status import main_system_status

        ss: list = []
        if getattr(args, "skip_repo_tests", False):
            ss.append("--skip-repo-tests")
        sys.exit(main_system_status(ss))

    if args.cmd == "phase6-prep-status":
        from trading_ai.phase6_prep.phase6_prep_status import build_phase6_prep_status
        import json as _json

        print(_json.dumps(build_phase6_prep_status(), indent=2, default=str))
        sys.exit(0)

    if args.cmd == "validate-env":
        from trading_ai.validate_env import run_validation

        sys.exit(run_validation())

    if args.cmd == "audit-env":
        from trading_ai.audit_env import run_audit

        sys.exit(run_audit())

    if args.cmd == "mcp-bridge":
        from trading_ai.bridge.mcp_server import main as mcp_main

        mcp_main()
        return

    if args.cmd == "claude-bridge":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        from trading_ai.bridge import api_client as bridge_api

        if args.bridge_action == "health":
            out = bridge_api.health_check()
        elif args.bridge_action == "kalshi-smoke":
            out = bridge_api.kalshi_smoke()
        elif args.bridge_action == "kalshi-dry-order":
            out = bridge_api.kalshi_dry_order(args.ticker, args.side)
        elif args.bridge_action == "pipeline-run":
            out = bridge_api.pipeline_run(webhook_telegrams=args.webhook_telegrams)
        elif args.bridge_action == "self-test":
            out = bridge_api.self_test()
            print(json.dumps(out, indent=2))
            sys.exit(0 if out.get("all_passed") else 1)
        else:
            parser.error("unknown bridge action")
        print(json.dumps(out, indent=2))
        return

    if args.cmd == "kalshi-smoke":
        from trading_ai.kalshi_cli import run_kalshi_smoke

        sys.exit(run_kalshi_smoke(settings))

    if args.cmd == "kalshi-dry-order":
        from trading_ai.kalshi_cli import run_kalshi_dry_order

        sys.exit(
            run_kalshi_dry_order(
                ticker=args.ticker,
                side=args.side,
                settings=settings,
            )
        )

    if args.cmd == "export-metrics":
        from trading_ai.automation.metrics_exporter import write_metrics_rollup

        write_metrics_rollup(settings)
        print("metrics written:", settings.metrics_json_path)
        return

    if args.cmd == "serve-api":
        from trading_ai.api.server import serve

        sys.exit(serve(settings, debug_bind=getattr(args, "debug", False)))

    if args.cmd == "api-status":
        from trading_ai.api.local_tools import run_api_status

        sys.exit(run_api_status(settings))

    if args.cmd == "api-stop-hint":
        from trading_ai.api.local_tools import run_api_stop_hint

        sys.exit(run_api_stop_hint(settings))

    if args.cmd == "run":
        if getattr(args, "dry_market_only", False):
            from trading_ai.market.candidate_adapter import unified_to_candidate
            from trading_ai.market.unified_catalog import build_pipeline_candidates

            unified_rows = build_pipeline_candidates(settings)
            print(f"candidates: {len(unified_rows)}")
            for u in unified_rows[:20]:
                print(unified_to_candidate(u).model_dump())
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
