"""CLI: ``python -m trading_ai.deployment <subcommand>``."""

from __future__ import annotations

from trading_ai.runtime_checks.ssl_guard import enforce_ssl

enforce_ssl()

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from trading_ai.deployment.deployment_checklist import run_deployment_checklist
from trading_ai.deployment.final_readiness_report import write_final_readiness_report
from trading_ai.deployment.live_micro_validation import (
    diagnose_micro_validation_trade,
    run_live_micro_validation_streak,
)
from trading_ai.deployment.readiness_decision import compute_final_readiness
from trading_ai.deployment.validation_products_runner import run_validation_products
from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.runtime_proof.live_execution_validation import run_gate_b_live_micro_validation


def _cli_runtime_root() -> Path:
    """Canonical ``EZRAS_RUNTIME_ROOT`` for deployment CLIs (matches daemon once/start/status/refresh)."""
    return resolve_ezras_runtime_root_for_daemon_authority()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m trading_ai.deployment")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "check-env",
        help="Print Coinbase operator env status (MISSING/SET lengths only; no secrets; no heredoc)",
    )
    sub.add_parser("checklist", help="Run deployment checklist (writes data/deployment/*)")
    p_micro = sub.add_parser("micro-validation", help="Run live micro-validation streak")
    p_micro.add_argument("--n", type=int, default=3, help="Round trips (default 3)")
    p_micro.add_argument("--product-id", default="BTC-USD", help="Coinbase product id")

    p_gb = sub.add_parser(
        "gate-b-live-micro",
        help="Gate B live Coinbase round-trip (writes execution_proof/gate_b_live_execution_validation.json)",
    )
    p_gb.add_argument("--quote-usd", type=float, default=10.0, help="Quote spend for market buy")
    p_gb.add_argument("--product-id", default="BTC-USD", help="Coinbase product id")
    p_gb.add_argument(
        "--skip-runtime-stability",
        action="store_true",
        help="Skip post-trade scheduler soak (faster; proof may show scheduler_stable false)",
    )

    p_tick = sub.add_parser(
        "gate-b-tick",
        help=(
            "Gate B Coinbase production tick: gate_b-scoped adaptive eval + engine on last scan rows "
            "(no orders; writes gate_b_last_production_tick.json)"
        ),
    )
    p_tick.add_argument(
        "--persist-gate-b-adaptive",
        action="store_true",
        help="Persist gate_b operating mode state (default: proof only unless env GATE_B_PRODUCTION_TICK_PERSIST_ADAPTIVE)",
    )

    p_ready = sub.add_parser("readiness", help="Compute final readiness (writes final_readiness.json)")
    p_ready.add_argument("--trade-id", default=None, help="Optional trade_id for Supabase/recon probe")

    sub.add_parser("final-report", help="Write data/deployment/final_readiness_report.txt from latest artifacts")

    p_diag = sub.add_parser(
        "diagnose-validation-run",
        help="Print compact diagnosis for a validation trade_id from live_validation_*.json",
    )
    p_diag.add_argument("trade_id", help="e.g. live_exec_5e8100782ab2")

    sub.add_parser(
        "validation-products",
        help="Show validation product priority, NTE allowlist, balances, and resolved choice",
    )

    p_refresh = sub.add_parser(
        "refresh-runtime-artifacts",
        help="Refresh control truth artifacts when dependencies change (no orders; no env mutation)",
    )
    p_refresh.add_argument("--force", action="store_true", help="Refresh all registered artifacts regardless of fingerprints")
    p_refresh.add_argument(
        "--show-stale-only",
        action="store_true",
        help="List stale artifact ids without writing",
    )
    p_refresh.add_argument(
        "--skip-advisory",
        action="store_true",
        help="Skip advisory truth_level artifacts (e.g. lessons_runtime_truth)",
    )
    p_refresh.add_argument(
        "--print-final-switch-truth",
        action="store_true",
        help="Embed gate_b_can_be_switched_live_now in printed summary",
    )

    p_av_st = sub.add_parser(
        "avenue-status",
        help="Universal live-switch + gaps snapshot for avenue A/B/C (artifact-driven)",
    )
    p_av_st.add_argument("--avenue", required=True, choices=["A", "B", "C"], help="A=Coinbase B=Kalshi C=Tastytrade")

    p_av_tick = sub.add_parser(
        "avenue-tick",
        help="Refresh runtime artifacts + avenue-specific tick (A: Gate B production tick; B/C: honest gap)",
    )
    p_av_tick.add_argument("--avenue", required=True, choices=["A", "B", "C"])
    p_av_tick.add_argument(
        "--persist-gate-b-adaptive",
        action="store_true",
        help="Avenue A only: persist gate_b adaptive state (same as gate-b-tick)",
    )

    sub.add_parser("write-remaining-gaps", help="Write data/control/universal_remaining_gaps.json")
    sub.add_parser("write-live-switch-truth", help="Write data/control/universal_live_switch_truth.json")

    p_ad_start = sub.add_parser(
        "avenue-a-daemon-start",
        help="Run Avenue A live daemon loop (set EZRAS_AVENUE_A_DAEMON_MODE + EZRAS_RUNTIME_ROOT)",
    )
    p_ad_start.add_argument("--quote-usd", type=float, default=10.0)
    p_ad_start.add_argument("--product-id", default="BTC-USD")

    p_ad_once = sub.add_parser("avenue-a-daemon-once", help="Single Avenue A daemon cycle (honest JSON to stdout)")
    p_ad_once.add_argument("--quote-usd", type=float, default=10.0)
    p_ad_once.add_argument("--product-id", default="BTC-USD")
    p_ad_once.add_argument("--skip-runtime-stability", action="store_true")

    sub.add_parser("avenue-a-daemon-status", help="Print avenue_a_daemon + policy snapshot JSON")
    sub.add_parser("avenue-a-daemon-stop", help="Best-effort runtime_runner.lock removal")

    p_orch_st = sub.add_parser(
        "orchestration-status",
        help="Print multi-bot orchestration registry summary JSON (no orders; governance layer only)",
    )
    p_orch_st.add_argument("--registry-path", default=None, help="Override EZRAS_BOT_REGISTRY_PATH")
    p_orch_st.add_argument(
        "--with-backbone",
        action="store_true",
        help="Include autonomous_backbone_status (reads EZRAS_RUNTIME_ROOT when set)",
    )
    p_orch_ceo = sub.add_parser(
        "orchestration-daily-ceo",
        help="Write canonical CEO daily orchestration review artifact (reads bot registry)",
    )
    p_orch_ceo.add_argument(
        "--registry-path",
        default=None,
        help="Override EZRAS_BOT_REGISTRY_PATH for this run",
    )
    p_orch_hb = sub.add_parser("orchestration-heartbeat", help="Touch heartbeat for a bot_id in registry")
    p_orch_hb.add_argument("--bot-id", required=True, help="Registered bot_id")
    p_orch_hb.add_argument("--registry-path", default=None)
    sub.add_parser(
        "orchestration-stale-sweep",
        help="Mark stale bots in registry (uses last_heartbeat_at)",
    )
    p_orch_ap = sub.add_parser(
        "orchestration-auto-promote",
        help="Run deterministic auto-promotion cycle (writes bot_auto_promotion_truth.json)",
    )
    p_orch_ap.add_argument("--registry-path", default=None)
    p_orch_cs = sub.add_parser(
        "orchestration-capital-scale",
        help="Run deterministic capital scale-up cycle (one step max per bot when contract passes)",
    )
    p_orch_cs.add_argument("--registry-path", default=None)
    p_orch_dc = sub.add_parser(
        "orchestration-deterministic-cycle",
        help="Run auto-promotion then capital scale-up (full deterministic governance tick)",
    )
    p_orch_dc.add_argument("--registry-path", default=None)
    p_orch_rtc = sub.add_parser(
        "refresh-orchestration-truth-chain",
        help="Write orchestration_truth_chain.json + detection snapshot (governance-only; no orders)",
    )
    p_orch_rtc.add_argument("--registry-path", default=None, help="Override EZRAS_BOT_REGISTRY_PATH")
    p_orch_fz = sub.add_parser(
        "orchestration-freeze",
        help="Set orchestration kill switch (global and/or avenue/gate/bot_id maps)",
    )
    p_orch_fz.add_argument("--global", dest="global_freeze", action="store_true", help="Freeze all orchestration")
    p_orch_fz.add_argument("--unfreeze-global", action="store_true", help="Clear global freeze")
    p_orch_q = sub.add_parser("orchestration-quarantine-bot", help="Freeze bot + observe-only (audited)")
    p_orch_q.add_argument("--bot-id", required=True)
    p_orch_q.add_argument("--reason", required=True)
    p_orch_q.add_argument("--operator", default="cli")
    p_orch_q.add_argument("--registry-path", default=None)
    p_orch_dis = sub.add_parser("orchestration-disable-bot", help="Disable bot in registry (audited)")
    p_orch_dis.add_argument("--bot-id", required=True)
    p_orch_dis.add_argument("--reason", required=True)
    p_orch_dis.add_argument("--operator", default="cli")
    p_orch_dis.add_argument("--registry-path", default=None)
    sub.add_parser("orchestration-list-bots", help="Compact bot summaries from registry (no secrets)")
    p_orch_lg = sub.add_parser(
        "orchestration-live-gate-check",
        help="Dry-run orchestration live gate for EZRAS_ACTIVE_ORCHESTRATION_BOT_ID (no venue orders)",
    )
    p_orch_lg.add_argument("--quote-usd", type=float, default=10.0)
    p_orch_lg.add_argument("--avenue", default="A")
    p_orch_lg.add_argument("--gate", default="gate_a")
    p_orch_lg.add_argument("--symbol", default="BTC-USD")
    p_orch_lg.add_argument("--registry-path", default=None)
    p_orch_lg.add_argument("--force-check", action="store_true", help="Evaluate gate even if EZRAS_ORCHESTRATION_LIVE_GATE unset")
    p_cap_chk = sub.add_parser(
        "capital-governor-check",
        help="Check whether quote_usd is allowed for EZRAS_ACTIVE_ORCHESTRATION_BOT_ID (no orders)",
    )
    p_cap_chk.add_argument("--quote-usd", type=float, default=10.0)
    p_cap_chk.add_argument("--avenue", default="A")
    p_cap_chk.add_argument("--gate", default="gate_a")
    p_cap_chk.add_argument("--registry-path", default=None)

    sub.add_parser(
        "write-final-pre-live-closure",
        help="Write consolidated live-switch closure bundle under data/control/",
    )

    p_dmat = sub.add_parser(
        "run-daemon-test-matrix",
        help="Run daemon verification matrix (fake/replay/live-proof scan) and write data/control artifacts",
    )
    p_dmat.add_argument(
        "--levels",
        default="fake,replay,live_proof",
        help="Comma-separated: fake, replay, live_proof (default: all three)",
    )

    sub.add_parser(
        "write-daemon-readiness",
        help="Write daemon readiness bundle (fake matrix tier + autonomous_live_readiness + final truth)",
    )
    sub.add_parser(
        "write-autonomous-live-readiness",
        help="Write autonomous_live_readiness_authority.json (+ rebuy stubs)",
    )
    sub.add_parser(
        "write-daemon-failure-truth",
        help="Write daemon_failure_injection_truth.json (uses fake matrix rows)",
    )

    sub.add_parser(
        "write-avenue-a-autonomous-runtime-truth",
        help="Write Avenue A autonomous runtime verification + cycle/lock/failure artifacts (no orders)",
    )
    sub.add_parser(
        "write-avenue-a-autonomous-authority",
        help="Write avenue_a_autonomous_authority.json (runtime merge for autonomous proof)",
    )
    sub.add_parser(
        "write-avenue-a-autonomous-blockers",
        help="Write avenue_a_autonomous_remaining_blockers.json from current artifacts",
    )
    sub.add_parser(
        "autonomous-verification-smoke",
        help="Write autonomous verification proof bundle (context loop + failure-stop + lock exclusivity); no orders",
    )
    sub.add_parser(
        "autonomous-failure-stop-verification-smoke",
        help="Write daemon_failure_stop_runtime_proof.json from runtime_runner_daemon_verification; no orders",
    )
    sub.add_parser(
        "autonomous-lock-exclusivity-verification-smoke",
        help="Write daemon_lock_exclusivity_runtime_proof.json from runtime verification + lock path; no orders",
    )
    sub.add_parser(
        "autonomous-proof-report",
        help="Print autonomous operator path + proof summary from on-disk artifacts (no orders)",
    )

    sub.add_parser("daemon-status", help="Armed/off + blockers + live matrix (no orders)")
    p_ds = sub.add_parser(
        "daemon-start-supervised",
        help="Print env exports for supervised daemon (does not start a supervisor process)",
    )
    p_ds.add_argument("--quote-usd", type=float, default=10.0)
    p_ds.add_argument("--product-id", default="BTC-USD")
    p_da = sub.add_parser(
        "daemon-start-autonomous",
        help="Print env exports for autonomous daemon (ARMED_BUT_OFF until enable contract + env)",
    )
    p_da.add_argument("--quote-usd", type=float, default=10.0)
    p_da.add_argument("--product-id", default="BTC-USD")
    sub.add_parser("daemon-stop", help="Alias: avenue-a-daemon-stop (runner lock)")
    p_arm = sub.add_parser(
        "daemon-arm-live",
        help="Write autonomous_daemon_live_enable.json (optional --confirm; no trades)",
    )
    p_arm.add_argument("--confirm", action="store_true", help="Set confirmed true")
    p_arm.add_argument("--operator", default="", help="Operator id or name")
    p_arm.add_argument("--note", default="", help="Note stored in artifact")
    sub.add_parser("daemon-disarm-live", help="Set autonomous_daemon_live_enable confirmed false")
    sub.add_parser(
        "write-final-daemon-truth",
        help="Write daemon_live authority + ARMED_BUT_OFF bundle (matrix, final classification)",
    )

    sub.add_parser(
        "write-supervised-live-truth",
        help="Recompute Avenue A supervised truth from ledger + gate_a proof (no orders)",
    )
    sub.add_parser(
        "write-supervised-session-summary",
        help="Roll up avenue_a_supervised_trade_log.jsonl (no orders)",
    )
    sub.add_parser(
        "write-daemon-enable-readiness-after-supervised",
        help="Write daemon_enable_readiness_after_supervised.json from real artifacts (no orders)",
    )
    sub.add_parser(
        "refresh-supervised-daemon-truth-chain",
        help=(
            "Idempotent: re-stamp daemon_live_switch_authority + env fingerprint for this shell, then "
            "write-supervised-live-truth chain (no orders). Use after fingerprint/runtime mismatch."
        ),
    )

    sub.add_parser(
        "avenue-a-go-live-verdict",
        help="Classify Avenue A daemon go-live posture from on-disk authority (no orders)",
    )
    sub.add_parser(
        "smoke-supervised-rebuy-loop",
        help="Write rebuy certification + inspect universal loop proof wiring (no orders)",
    )
    p_sm_bb = sub.add_parser(
        "smoke-autonomous-backbone",
        help="Run multi-step orchestration smoke (seed specialists, CEO, promotion, truth chain; no orders)",
    )
    p_sm_bb.add_argument("--registry-path", required=True, help="Temp or dedicated bot_registry.json path")
    sub.add_parser(
        "orchestration-seed-canonical-specialists",
        help="Register canonical specialist bots for Avenue A / gate_a if missing (shadow band)",
    )
    sub.add_parser(
        "avenue-a-status",
        help="Avenue A Gate A + Gate B operator bundle (writes data/control/avenue_a_operator_status.json)",
    )
    sub.add_parser(
        "avenue-a-capital-status",
        help="Deployable capital split Gate A / Gate B + idle-loan policy (no orders)",
    )
    sub.add_parser(
        "avenue-a-gate-a-status",
        help="Gate A universe policy + ranked snapshot from NTE priority (deterministic placeholder rows)",
    )
    sub.add_parser(
        "avenue-a-gate-b-status",
        help="Gate B momentum lane configuration (profit zone, limits, honesty)",
    )
    sub.add_parser(
        "gate-a-selection-smoke",
        help="Deterministic Gate A selection snapshot (public tickers; writes gate_a_selection_snapshot.json)",
    )
    p_gbs = sub.add_parser(
        "gate-b-selection-smoke",
        help="Deterministic Gate B gainers ranking snapshot (writes gate_b_selection_snapshot.json)",
    )
    p_gbs.add_argument(
        "--deployable-usd",
        type=float,
        default=None,
        help="Optional deployable USD for 50/50 split truth; omit uses $100 literal demo budget label",
    )
    p_csr = sub.add_parser(
        "coinbase-selection-report",
        help="Combined Gate A/B selection + 50/50 capital split snapshot JSON (no orders)",
    )
    p_csr.add_argument("--deployable-usd", type=float, default=None, help="Optional deployable USD for split")

    sub.add_parser(
        "controlled-live-readiness",
        help=(
            "Single JSON: env/SSL/Coinbase, Gate A/B blockers, Avenue A supervised+autonomous, Supabase schema, proof alignment "
            "(writes data/control/controlled_live_readiness.json)"
        ),
    )

    args = p.parse_args()
    if "EZRAS_BOT_REGISTRY_PATH" not in os.environ:
        pass  # optional; orchestration CLIs use default path or --registry-path

    if args.cmd == "check-env":
        from trading_ai.deployment.check_env import format_check_env_lines, run_check_env

        data = run_check_env()
        print("\n".join(data["lines"]) + "\n", end="")
        return 0 if data.get("coinbase_credentials_ok") else 12
    if args.cmd == "checklist":
        out = run_deployment_checklist(write_files=True)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ready_for_live_micro_validation") else 2
    if args.cmd == "micro-validation":
        out = run_live_micro_validation_streak(n=args.n, product_id=args.product_id)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("live_validation_streak_passed") else 3
    if args.cmd == "gate-b-live-micro":
        out = run_gate_b_live_micro_validation(
            quote_usd=float(args.quote_usd),
            product_id=str(args.product_id),
            include_runtime_stability=not bool(args.skip_runtime_stability),
        )
        print(json.dumps(out, indent=2, default=str))
        ok = bool(out.get("FINAL_EXECUTION_PROVEN") or (out.get("proof") or {}).get("FINAL_EXECUTION_PROVEN"))
        return 0 if ok else 6
    if args.cmd == "gate-b-tick":
        from trading_ai.deployment.gate_b_production_tick import run_gate_b_production_tick

        out = run_gate_b_production_tick(persist_gate_b_adaptive_state=bool(args.persist_gate_b_adaptive))
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("tick_ok") else 7
    if args.cmd == "readiness":
        out = compute_final_readiness(trade_id_probe=args.trade_id, write_files=True)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ready_for_first_20") else 4
    if args.cmd == "final-report":
        txt = write_final_readiness_report(write_file=True)
        print(txt[:12000])
        return 0
    if args.cmd == "diagnose-validation-run":
        out = diagnose_micro_validation_trade(args.trade_id)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("found") else 5
    if args.cmd == "validation-products":
        payload = run_validation_products(quote_notional=10.0)
        print(json.dumps(payload, indent=2, default=str))
        return 0
    if args.cmd == "refresh-runtime-artifacts":
        from trading_ai.reports.runtime_artifact_refresh_manager import run_refresh_runtime_artifacts

        include_adv = not bool(args.skip_advisory)
        out = run_refresh_runtime_artifacts(
            force=bool(args.force),
            show_stale_only=bool(args.show_stale_only),
            include_advisory=include_adv,
            print_final_switch_truth=bool(args.print_final_switch_truth),
        )
        summary = {
            "refresh_complete_and_trustworthy": out.get("refresh_complete_and_trustworthy"),
            "artifacts_refreshed": out.get("artifacts_refreshed"),
            "artifacts_skipped_as_fresh": out.get("artifacts_skipped_as_fresh"),
            "stale_artifacts_detected": out.get("stale_artifacts_detected"),
            "refresh_failures": out.get("refresh_failures"),
            "gate_b_can_be_switched_live_now": out.get("gate_b_can_be_switched_live_now"),
            "authoritative_switch_artifact": out.get("authoritative_switch_artifact"),
            "runtime_artifact_refresh_truth": str(Path(out["runtime_root"]) / "data" / "control" / "runtime_artifact_refresh_truth.json"),
        }
        print(json.dumps(summary, indent=2, default=str))
        if out.get("refresh_failures"):
            return 8
        return 0
    if args.cmd == "avenue-status":
        from trading_ai.universal_execution.avenue_deployment_dispatch import run_avenue_status

        out = run_avenue_status(args.avenue)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "avenue-tick":
        from trading_ai.universal_execution.avenue_deployment_dispatch import run_avenue_tick

        out = run_avenue_tick(args.avenue, persist_gate_b_adaptive=bool(args.persist_gate_b_adaptive))
        print(json.dumps(out, indent=2, default=str))
        if out.get("error"):
            return 9
        if args.avenue == "A":
            t = out.get("gate_b_production_tick") or {}
            return 0 if t.get("tick_ok") else 7
        return 0
    if args.cmd == "write-remaining-gaps":
        from trading_ai.universal_execution.avenue_deployment_dispatch import run_write_remaining_gaps

        out = run_write_remaining_gaps()
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "write-live-switch-truth":
        from trading_ai.universal_execution.avenue_deployment_dispatch import run_write_live_switch_truth

        out = run_write_live_switch_truth()
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-daemon-start":
        from trading_ai.orchestration.avenue_a_live_daemon import run_avenue_a_daemon_forever

        rt = _cli_runtime_root()
        run_avenue_a_daemon_forever(runtime_root=rt, quote_usd=float(args.quote_usd), product_id=str(args.product_id))
        return 0
    if args.cmd == "avenue-a-daemon-once":
        from trading_ai.orchestration.avenue_a_live_daemon import run_avenue_a_daemon_once

        rt = _cli_runtime_root()
        out = run_avenue_a_daemon_once(
            runtime_root=rt,
            quote_usd=float(args.quote_usd),
            product_id=str(args.product_id),
            include_runtime_stability=not bool(args.skip_runtime_stability),
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 11
    if args.cmd == "avenue-a-daemon-status":
        from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_status

        rt = _cli_runtime_root()
        out = avenue_a_daemon_status(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-daemon-stop":
        from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_stop

        rt = _cli_runtime_root()
        out = avenue_a_daemon_stop(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "orchestration-status":

        from trading_ai.global_layer.bot_registry import load_registry
        from trading_ai.global_layer.execution_authority import load_authority_registry
        from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch

        rp = (
            Path(str(args.registry_path)).expanduser().resolve()
            if getattr(args, "registry_path", None)
            else None
        )
        envp = (os.environ.get("EZRAS_BOT_REGISTRY_PATH") or "").strip()
        path = rp or (Path(envp).expanduser().resolve() if envp else None)
        from trading_ai.global_layer.orchestration_truth_chain import build_orchestration_truth_chain

        reg = load_registry(path)
        chain = build_orchestration_truth_chain(registry_path=path)
        out = {
            "registry_truth_version": reg.get("truth_version"),
            "bot_count": len(reg.get("bots") or []),
            "bots": reg.get("bots") or [],
            "execution_authority": load_authority_registry(),
            "orchestration_kill_switch": load_kill_switch(),
            "truth_chain_summary": {
                "blockers": chain.get("blockers"),
                "readiness": chain.get("readiness"),
                "authority_drift_blocked": (chain.get("authority_drift") or {}).get("blocked"),
                "next_operator_commands": chain.get("next_operator_commands"),
                "runtime_root": chain.get("runtime_root"),
                "historical_note": chain.get("historical_note"),
            },
        }
        try:
            from trading_ai.deployment.operator_env_contracts import build_env_config_blocker_summary

            rr = chain.get("runtime_root") or {}
            rtp = str(rr.get("path") or os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
            if rtp:
                out["operator_env_config_blockers"] = build_env_config_blocker_summary(
                    runtime_root=Path(rtp).expanduser().resolve(),
                    require_supervised_confirm=True,
                )
        except Exception:
            pass
        out["operator_orchestration_path_summary"] = {
            "orchestration_blockers": chain.get("blockers"),
            "supervised_live_operation": (chain.get("readiness") or {}).get("supervised_live_operation"),
            "autonomous_operation": (chain.get("readiness") or {}).get("autonomous_operation"),
            "honesty": (
                "supervised_live_operation / autonomous_operation come from orchestration truth chain readiness; "
                "operator_env_config_blockers (when present) is process-env snapshot for Avenue A Gate A shell setup."
            ),
        }
        if getattr(args, "with_backbone", False):

            from trading_ai.global_layer.autonomous_backbone_status import build_autonomous_backbone_status

            try:
                rt_bb = Path(_cli_runtime_root())
            except Exception:
                rt_bb = None
            out["autonomous_backbone_status"] = build_autonomous_backbone_status(
                registry_path=path,
                runtime_root=rt_bb,
                write_file=True,
            )
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:240_000])
        return 0
    if args.cmd == "orchestration-daily-ceo":

        from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = write_daily_ceo_review(registry_path=path)
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:120_000])
        return 0
    if args.cmd == "orchestration-heartbeat":

        from trading_ai.global_layer.orchestration_heartbeat import touch_heartbeat

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = touch_heartbeat(str(args.bot_id), path=path)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "orchestration-stale-sweep":
        from trading_ai.global_layer.orchestration_heartbeat import run_stale_sweep

        out = run_stale_sweep()
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "orchestration-auto-promote":

        from trading_ai.global_layer.deterministic_autonomous_orchestration import run_auto_promotion_cycle

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = run_auto_promotion_cycle(registry_path=path)
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:200_000])
        return 0
    if args.cmd == "orchestration-capital-scale":

        from trading_ai.global_layer.deterministic_autonomous_orchestration import run_capital_scale_up_cycle

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = run_capital_scale_up_cycle(registry_path=path)
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:120_000])
        return 0
    if args.cmd == "orchestration-deterministic-cycle":

        from trading_ai.global_layer.deterministic_autonomous_orchestration import run_full_deterministic_cycle

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = run_full_deterministic_cycle(registry_path=path)
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:200_000])
        return 0
    if args.cmd == "refresh-orchestration-truth-chain":

        from trading_ai.global_layer.orchestration_truth_chain import write_orchestration_truth_chain

        rp = getattr(args, "registry_path", None)
        path = Path(str(rp)).expanduser().resolve() if rp else None
        out = write_orchestration_truth_chain(registry_path=path)
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False)[:240_000])
        return 0
    if args.cmd == "orchestration-freeze":
        from trading_ai.global_layer.orchestration_kill_switch import load_kill_switch, save_kill_switch

        c = load_kill_switch()
        if getattr(args, "unfreeze_global", False):
            c["orchestration_frozen"] = False
        elif getattr(args, "global_freeze", False):
            c["orchestration_frozen"] = True
        save_kill_switch(c)
        print(json.dumps({"ok": True, "orchestration_kill_switch": load_kill_switch()}, indent=2, default=str))
        return 0
    if args.cmd == "orchestration-quarantine-bot":

        from trading_ai.global_layer.orchestration_operator_actions import quarantine_bot

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = quarantine_bot(
            str(args.bot_id),
            reason=str(args.reason),
            operator=str(args.operator),
            registry_path=path,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if args.cmd == "orchestration-disable-bot":

        from trading_ai.global_layer.orchestration_operator_actions import disable_bot

        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        out = disable_bot(
            str(args.bot_id),
            reason=str(args.reason),
            operator=str(args.operator),
            registry_path=path,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if args.cmd == "orchestration-list-bots":
        from trading_ai.global_layer.orchestration_operator_actions import list_bot_summaries

        out = list_bot_summaries()
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "orchestration-live-gate-check":

        from trading_ai.global_layer.bot_registry import get_bot
        from trading_ai.global_layer.orchestration_live_execution_gate import evaluate_live_execution_gate

        bid = (os.environ.get("EZRAS_ACTIVE_ORCHESTRATION_BOT_ID") or "").strip()
        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        if not bid:
            print(json.dumps({"ok": False, "error": "set_EZRAS_ACTIVE_ORCHESTRATION_BOT_ID"}, indent=2))
            return 1
        bot = get_bot(bid, path=path)
        if not bot:
            print(json.dumps({"ok": False, "error": f"unknown_bot:{bid}"}, indent=2))
            return 1
        out = evaluate_live_execution_gate(
            bot,
            quote_usd=float(args.quote_usd),
            avenue=str(args.avenue),
            gate=str(args.gate),
            route="default",
            symbol=str(args.symbol),
            registry_path=path,
            force_check=bool(args.force_check),
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("allowed") else 1
    if args.cmd == "capital-governor-check":

        from trading_ai.global_layer.bot_registry import get_bot
        from trading_ai.global_layer.capital_governor import check_live_quote_allowed, sync_registry_from_bot

        bid = (os.environ.get("EZRAS_ACTIVE_ORCHESTRATION_BOT_ID") or "").strip()
        rp = getattr(args, "registry_path", None)
        path = Path(rp).expanduser().resolve() if rp else None
        if not bid:
            print(json.dumps({"ok": False, "error": "set_EZRAS_ACTIVE_ORCHESTRATION_BOT_ID"}, indent=2))
            return 1
        bot = get_bot(bid, path=path)
        if not bot:
            print(json.dumps({"ok": False, "error": f"unknown_bot:{bid}"}, indent=2))
            return 1
        sync_registry_from_bot(bot)
        ok, why, diag = check_live_quote_allowed(
            bot,
            float(args.quote_usd),
            avenue=str(args.avenue),
            gate=str(args.gate),
            route="default",
        )
        print(json.dumps({"ok": ok, "reason": why, "diagnostics": diag, "bot_id": bid}, indent=2, default=str))
        return 0 if ok else 1
    if args.cmd == "write-final-pre-live-closure":

        from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

        rt = _cli_runtime_root()
        out = write_live_switch_closure_bundle(runtime_root=rt, trigger_surface="deployment_cli", reason="write_final_pre_live_closure")
        print(json.dumps({"written": out.get("written"), "section_errors": out.get("section_errors")}, indent=2, default=str))
        return 0 if not out.get("section_errors") else 12
    if args.cmd == "run-daemon-test-matrix":

        from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_verification_artifacts

        rt = _cli_runtime_root()
        raw = [x.strip() for x in str(args.levels).split(",") if x.strip()]
        out = write_daemon_verification_artifacts(runtime_root=rt, levels=tuple(raw) if raw else None)
        print(json.dumps({"row_count": out["matrix"].get("row_count"), "summary": out["matrix"].get("summary")}, indent=2, default=str))
        return 0
    if args.cmd == "write-daemon-readiness":

        from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_readiness_bundle

        rt = _cli_runtime_root()
        out = write_daemon_readiness_bundle(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "write-autonomous-live-readiness":

        from trading_ai.daemon_testing.daemon_artifact_writers import write_autonomous_live_readiness_only

        rt = _cli_runtime_root()
        out = write_autonomous_live_readiness_only(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "write-daemon-failure-truth":

        from trading_ai.daemon_testing.daemon_artifact_writers import write_daemon_failure_truth_artifact

        rt = _cli_runtime_root()
        out = write_daemon_failure_truth_artifact(runtime_root=rt)
        print(json.dumps({"written": True, "path": "data/control/daemon_failure_injection_truth.json", "keys": list(out.get("failures", {}).keys())[:5]}, indent=2, default=str))
        return 0
    if args.cmd == "write-avenue-a-autonomous-runtime-truth":

        from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import write_all_avenue_a_autonomous_runtime_artifacts

        rt = _cli_runtime_root()
        out = write_all_avenue_a_autonomous_runtime_artifacts(runtime_root=rt)
        print(json.dumps({"written": True, "authority": out.get("authority", {}).get("closure_line")}, indent=2, default=str))
        return 0
    if args.cmd == "write-avenue-a-autonomous-authority":

        from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import write_avenue_a_autonomous_authority

        rt = _cli_runtime_root()
        out = write_avenue_a_autonomous_authority(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str)[:16000])
        return 0
    if args.cmd == "write-avenue-a-autonomous-blockers":

        from trading_ai.orchestration.avenue_a_autonomous_runtime_truth import write_avenue_a_autonomous_remaining_blockers

        rt = _cli_runtime_root()
        out = write_avenue_a_autonomous_remaining_blockers(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "autonomous-verification-smoke":

        from trading_ai.orchestration.autonomous_verification_proofs import write_autonomous_verification_proof_bundle

        rt = _cli_runtime_root()
        out = write_autonomous_verification_proof_bundle(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "autonomous-failure-stop-verification-smoke":

        from trading_ai.orchestration.autonomous_verification_proofs import write_daemon_failure_stop_runtime_proof

        rt = _cli_runtime_root()
        out = write_daemon_failure_stop_runtime_proof(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "autonomous-lock-exclusivity-verification-smoke":

        from trading_ai.orchestration.autonomous_verification_proofs import write_daemon_lock_exclusivity_runtime_proof

        rt = _cli_runtime_root()
        out = write_daemon_lock_exclusivity_runtime_proof(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "autonomous-proof-report":

        from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path
        from trading_ai.orchestration.autonomous_verification_proofs import write_autonomous_verification_proof_bundle

        rt = _cli_runtime_root()
        bundle = write_autonomous_verification_proof_bundle(runtime_root=rt)
        report = build_autonomous_operator_path(runtime_root=rt)
        print(json.dumps({"proof_bundle": bundle, "operator_path": report}, indent=2, default=str)[:24000])
        return 0
    if args.cmd == "daemon-status":

        from trading_ai.deployment.daemon_operator_cli import build_daemon_operator_status

        rt = _cli_runtime_root()
        out = build_daemon_operator_status(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "daemon-start-supervised":
        rt = str(_cli_runtime_root())
        print(
            json.dumps(
                {
                    "instruction": "Export then run avenue-a-daemon-start under your process supervisor",
                    "EZRAS_RUNTIME_ROOT": rt,
                    "EZRAS_AVENUE_A_DAEMON_MODE": "supervised_live",
                    "example": f"export EZRAS_RUNTIME_ROOT={rt} EZRAS_AVENUE_A_DAEMON_MODE=supervised_live && python3 -m trading_ai.deployment avenue-a-daemon-start --quote-usd {args.quote_usd} --product-id {args.product_id}",
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "daemon-start-autonomous":
        rt = str(_cli_runtime_root())
        print(
            json.dumps(
                {
                    "instruction": "Default ARMED_BUT_OFF: loops refresh truth; venue orders need dual gate",
                    "EZRAS_RUNTIME_ROOT": rt,
                    "EZRAS_AVENUE_A_DAEMON_MODE": "autonomous_live",
                    "dual_gate": "autonomous_daemon_live_enable.json + EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true",
                    "example": f"export EZRAS_RUNTIME_ROOT={rt} EZRAS_AVENUE_A_DAEMON_MODE=autonomous_live && python3 -m trading_ai.deployment avenue-a-daemon-start --quote-usd {args.quote_usd} --product-id {args.product_id}",
                },
                indent=2,
            )
        )
        return 0
    if args.cmd == "daemon-stop":

        from trading_ai.orchestration.avenue_a_live_daemon import avenue_a_daemon_stop

        rt = _cli_runtime_root()
        out = avenue_a_daemon_stop(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "daemon-arm-live":

        from trading_ai.orchestration.autonomous_daemon_live_contract import arm_autonomous_daemon_live_enable_file

        rt = _cli_runtime_root()
        out = arm_autonomous_daemon_live_enable_file(
            runtime_root=rt,
            confirmed=bool(args.confirm),
            avenue_ids=["A"],
            gate_ids=["gate_a"],
            operator=str(args.operator or ""),
            note=str(args.note or ""),
        )
        print(json.dumps({"written": True, "payload": out}, indent=2, default=str))
        return 0
    if args.cmd == "daemon-disarm-live":

        from trading_ai.orchestration.autonomous_daemon_live_contract import disarm_autonomous_daemon_live_enable_file

        rt = _cli_runtime_root()
        out = disarm_autonomous_daemon_live_enable_file(runtime_root=rt)
        print(json.dumps({"disarmed": True, "payload": out}, indent=2, default=str))
        return 0
    if args.cmd == "write-final-daemon-truth":

        from trading_ai.orchestration.armed_but_off_authority import write_all_armed_but_off_artifacts
        from trading_ai.orchestration.daemon_live_authority import write_all_daemon_live_artifacts

        rt = _cli_runtime_root()
        d = write_all_daemon_live_artifacts(runtime_root=rt)
        a = write_all_armed_but_off_artifacts(runtime_root=rt)
        print(json.dumps({"daemon_live_keys": list(d.keys()), "armed_but_off_keys": list(a.keys())}, indent=2, default=str))
        return 0
    if args.cmd == "write-supervised-live-truth":

        from trading_ai.orchestration.supervised_avenue_a_truth import write_all_supervised_artifacts_cli

        rt = _cli_runtime_root()
        out = write_all_supervised_artifacts_cli(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "write-supervised-session-summary":

        from trading_ai.orchestration.supervised_avenue_a_truth import write_supervised_session_summary

        rt = _cli_runtime_root()
        out = write_supervised_session_summary(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "write-daemon-enable-readiness-after-supervised":

        from trading_ai.orchestration.supervised_avenue_a_truth import build_daemon_enable_readiness_after_supervised

        rt = _cli_runtime_root()
        out = build_daemon_enable_readiness_after_supervised(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "refresh-supervised-daemon-truth-chain":

        from trading_ai.orchestration.supervised_avenue_a_truth import refresh_supervised_daemon_truth_chain

        rt = _cli_runtime_root()
        out = refresh_supervised_daemon_truth_chain(runtime_root=rt)
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-go-live-verdict":

        from trading_ai.deployment.autonomous_smoke import avenue_a_go_live_verdict

        out = avenue_a_go_live_verdict(runtime_root=_cli_runtime_root())
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "smoke-supervised-rebuy-loop":
        from trading_ai.deployment.autonomous_smoke import run_smoke_supervised_rebuy_loop

        out = run_smoke_supervised_rebuy_loop(runtime_root=_cli_runtime_root())
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "smoke-autonomous-backbone":

        from trading_ai.deployment.autonomous_smoke import run_smoke_autonomous_backbone

        rp = Path(str(args.registry_path)).expanduser().resolve()
        out = run_smoke_autonomous_backbone(registry_path=rp, runtime_root=_cli_runtime_root())
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 2
    if args.cmd == "orchestration-seed-canonical-specialists":

        from trading_ai.global_layer.canonical_specialist_seed import ensure_canonical_specialists

        rp = (os.environ.get("EZRAS_BOT_REGISTRY_PATH") or "").strip()
        path = Path(rp).expanduser().resolve() if rp else None
        out = ensure_canonical_specialists(registry_path=path)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if args.cmd == "avenue-a-status":
        from trading_ai.shark.coinbase_spot.avenue_a_operator_status import write_avenue_a_operator_status_artifact

        out = write_avenue_a_operator_status_artifact(runtime_root=_cli_runtime_root())
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-capital-status":
        from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status

        full = build_avenue_a_operator_status(runtime_root=_cli_runtime_root())
        print(json.dumps({"capital": full.get("capital"), "truth_version": full.get("truth_version")}, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-gate-a-status":
        from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status

        full = build_avenue_a_operator_status(runtime_root=_cli_runtime_root())
        print(json.dumps({"gate_a": full.get("gate_a"), "truth_version": full.get("truth_version")}, indent=2, default=str))
        return 0
    if args.cmd == "avenue-a-gate-b-status":
        from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status

        full = build_avenue_a_operator_status(runtime_root=_cli_runtime_root())
        print(json.dumps({"gate_b": full.get("gate_b"), "truth_version": full.get("truth_version")}, indent=2, default=str))
        return 0
    if args.cmd == "gate-a-selection-smoke":
        from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import run_gate_a_product_selection
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        rt = _cli_runtime_root()
        out = run_gate_a_product_selection(
            runtime_root=rt,
            client=CoinbaseClient(),
            quote_usd=10.0,
            explicit_product_id=None,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("selected_product") else 3
    if args.cmd == "gate-b-selection-smoke":
        from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import run_gate_b_gainers_selection
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        rt = _cli_runtime_root()
        dep_gb = getattr(args, "deployable_usd", None)
        out = run_gate_b_gainers_selection(
            runtime_root=rt,
            client=CoinbaseClient(),
            deployable_quote_usd=float(dep_gb) if dep_gb is not None else None,
            capital_budget_usd=None if dep_gb is not None else 100.0,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.cmd == "coinbase-selection-report":
        from trading_ai.orchestration.coinbase_gate_selection.coinbase_capital_split import compute_coinbase_gate_capital_split
        from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import run_gate_a_product_selection
        from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import run_gate_b_gainers_selection
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        rt = _cli_runtime_root()
        dep = getattr(args, "deployable_usd", None)
        split = compute_coinbase_gate_capital_split(dep, runtime_root=rt)
        ga = run_gate_a_product_selection(runtime_root=rt, client=CoinbaseClient(), quote_usd=10.0, explicit_product_id=None)
        gb = run_gate_b_gainers_selection(
            runtime_root=rt,
            client=CoinbaseClient(),
            deployable_quote_usd=float(dep) if dep is not None else None,
            capital_budget_usd=None if dep is not None else 100.0,
        )
        print(
            json.dumps(
                {"capital_split": split, "gate_a_selection": ga, "gate_b_selection": gb},
                indent=2,
                default=str,
            )
        )
        return 0 if split.get("ok") or dep is None else 4
    if args.cmd == "controlled-live-readiness":
        from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report

        out = build_controlled_live_readiness_report(runtime_root=_cli_runtime_root(), write_artifact=True)
        print(json.dumps(out, indent=2, default=str)[:400_000])
        rc = 0
        if not out.get("rollup_answers", {}).get("are_env_ssl_coinbase_commands_clean"):
            rc = 12
        return rc
    return 1


if __name__ == "__main__":
    sys.exit(main())
