"""CLI: ``python -m trading_ai.runtime`` — supervised autonomy (no venue live orders).

Heavy imports are deferred inside handlers to keep cold-start imports minimal.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _run_accelerated_sim(*, cycles: int, runtime_root: Path, skip_models: bool) -> Dict[str, Any]:
    from trading_ai.runtime.now_live_proof import run_authoritative_live_guard_proof, write_now_live_autonomy_artifacts
    from trading_ai.runtime.operating_system import assert_live_trading_env_disabled, enforce_non_live_env_defaults
    from trading_ai.runtime.operating_system import run_role_supervisor_once
    from trading_ai.simulation.nonlive import assert_nonlive_for_simulation

    enforce_non_live_env_defaults()
    ok, why = assert_live_trading_env_disabled()
    if not ok:
        return {"ok": False, "blocked": True, "reason": why}
    write_now_live_autonomy_artifacts(runtime_root=runtime_root)
    ran_ops: List[str] = []
    ran_rs: List[str] = []
    for i in range(max(1, int(cycles))):
        o = run_role_supervisor_once(role="ops", runtime_root=runtime_root, skip_models=skip_models, force_all_due=True)
        r = run_role_supervisor_once(
            role="research", runtime_root=runtime_root, skip_models=skip_models, force_all_due=True
        )
        ran_ops.extend(list(o.get("ran") or []))
        ran_rs.extend(list(r.get("ran") or []))
        if i % 3 == 0:
            time.sleep(0.01)

    assert_nonlive_for_simulation()
    lg_ok, lg = run_authoritative_live_guard_proof(runtime_root=runtime_root)
    ctrl = runtime_root / "data" / "control"
    sim_trade_count = 0
    try:
        st_doc = json.loads((ctrl / "sim_trade_log.json").read_text(encoding="utf-8"))
        sim_trade_count = int(st_doc.get("count") or 0)
    except Exception:
        pass
    chain_summary = (runtime_root / "data" / "control" / "simulated_fill_chain" / "reconciliation_summary.json").is_file()
    terminal_fills = 0
    try:
        fl_doc = json.loads((ctrl / "sim_fill_log.json").read_text(encoding="utf-8"))
        for row in list(fl_doc.get("fills") or []):
            ph = str((row.get("fill") or {}).get("phase") or "")
            if ph in ("filled", "canceled", "rejected"):
                terminal_fills += 1
    except Exception:
        pass
    fill_proof_ok = bool(chain_summary or sim_trade_count > 0 or terminal_fills > 0)
    verdict: Dict[str, Any] = {
        "truth_version": "accelerated_autonomy_sim_verdict_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycles": int(cycles),
        "runtime_root": str(runtime_root),
        "ops_loops_touched": sorted(set(ran_ops)),
        "research_loops_touched": sorted(set(ran_rs)),
        "scanner_snapshot_exists": (runtime_root / "data" / "control" / "scanner_autonomy_snapshot.json").is_file(),
        "simulated_fill_summary_exists": chain_summary,
        "sim_trade_count": sim_trade_count,
        "terminal_fill_events": terminal_fills,
        "fill_proof_ok": fill_proof_ok,
        "regression_drift_exists": (ctrl / "regression_drift.json").is_file(),
        "pnl_review_exists": (runtime_root / "data" / "control" / "pnl_review.json").is_file(),
        "comparisons_exists": (runtime_root / "data" / "control" / "performance_comparisons.json").is_file(),
        "research_regression_exists": (runtime_root / "data" / "control" / "research_regression_drift.json").is_file(),
        "sim_pnl_exists": (ctrl / "sim_pnl.json").is_file(),
        "sim_tasks_exists": (ctrl / "sim_tasks.json").is_file(),
        "sim_lessons_exists": (ctrl / "sim_lessons.json").is_file(),
        "sim_24h_summary_exists": (ctrl / "sim_24h_summary.json").is_file(),
        "live_guard_ok": lg_ok,
        "live_guard": lg,
    }
    ok2 = (
        verdict["scanner_snapshot_exists"]
        and verdict["fill_proof_ok"]
        and verdict["pnl_review_exists"]
        and verdict["comparisons_exists"]
        and verdict["research_regression_exists"]
        and verdict["sim_pnl_exists"]
        and verdict["sim_tasks_exists"]
        and verdict["sim_lessons_exists"]
        and verdict["sim_24h_summary_exists"]
        and verdict["regression_drift_exists"]
        and bool(lg_ok)
    )
    verdict["ok"] = bool(ok2)
    p = runtime_root / "data" / "control" / "accelerated_autonomy_sim_verdict.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(verdict, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    _setup_logging()
    p = argparse.ArgumentParser(
        prog="python -m trading_ai.runtime",
        description="Supervised now-live autonomy stack (venue live orders remain env-disabled)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tick", help="Run one legacy role tick (ops/research)")
    t.add_argument("--role", required=True, choices=["ops", "research"])
    t.add_argument("--runtime-root", default=None, help="Optional EZRAS_RUNTIME_ROOT override")
    t.add_argument(
        "--skip-models",
        action="store_true",
        help="Research tick: skip model-heavy review steps",
    )

    d = sub.add_parser("daemon", help="Run role daemon loop (supervisor + role lock)")
    d.add_argument("--role", required=True, choices=["ops", "research"])
    d.add_argument("--runtime-root", default=None)
    d.add_argument("--interval-sec", type=float, default=60.0, help="Sleep between cycles (default 60)")
    d.add_argument("--holder-id", default=None, help="Lock holder id (default pid-based)")
    d.add_argument("--skip-models", action="store_true", help="Research daemon: skip model-heavy reviews")
    d.add_argument("--cycles", type=int, default=0, help="Stop after N cycles (0=forever)")
    d.add_argument("--force-all-due", action="store_true", help="Run every loop each cycle (tests/smoke)")

    s1 = sub.add_parser("supervisor-once", help="Single supervisor step (no lock acquisition)")
    s1.add_argument("--role", required=True, choices=["ops", "research"])
    s1.add_argument("--runtime-root", default=None)
    s1.add_argument("--skip-models", action="store_true")
    s1.add_argument("--force-all-due", action="store_true")

    sub.add_parser("write-now-live-artifacts", help="Write full_autonomy_* proof JSON under data/control/")
    sub.add_parser("live-guard-proof", help="Executable live-env-off proof artifact")

    sim = sub.add_parser("accelerated-sim", help="Multi-cycle deterministic autonomy proof (not wall-clock 24h)")
    sim.add_argument("--cycles", type=int, default=14)
    sim.add_argument("--runtime-root", default=None)
    sim.add_argument("--skip-models", action="store_true")

    lk = sub.add_parser("role-lock-smoke", help="Prove second holder cannot take the same role lock")
    lk.add_argument("--role", required=True, choices=["ops", "research"])
    lk.add_argument("--runtime-root", default=None)

    sub.add_parser(
        "autonomy-deploy-preflight",
        help="Fail-closed deploy checks; writes data/control/autonomy_deploy_preflight.json",
    )
    sub.add_parser(
        "systemd-unit-contract-verify",
        help="Verify docs/systemd/*.service templates; writes systemd_unit_contract_verify.json",
    )

    f60t = sub.add_parser("first-60-live-ops-tick", help="One first-60 calendar automation tick (envelopes + heartbeat)")
    f60t.add_argument("--runtime-root", default=None)
    f60t.add_argument(
        "--force",
        action="store_true",
        help="Rewrite daily envelope even if already written for current UTC day",
    )

    f60d = sub.add_parser(
        "first-60-live-ops-daemon",
        help="Loop first-60 ticks forever (SIGINT/SIGTERM stop). Sleep: --interval-sec or EZRAS_FIRST_60_DAEMON_SLEEP_SEC (default 120).",
    )
    f60d.add_argument("--runtime-root", default=None)
    f60d.add_argument("--interval-sec", type=float, default=None)

    args = p.parse_args()

    from trading_ai.runtime.operating_system import (
        enforce_non_live_env_defaults,
        release_role_lock,
        run_role_supervisor_once,
        tick_ops_once,
        tick_research_once,
        try_acquire_role_lock,
    )
    from trading_ai.runtime_paths import ezras_runtime_root
    from trading_ai.simulation.nonlive import nonlive_env_ok

    enforce_non_live_env_defaults()

    rt = Path(args.runtime_root).resolve() if getattr(args, "runtime_root", None) else ezras_runtime_root()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(rt)

    if args.cmd != "live-guard-proof":
        ok_env, why_env = nonlive_env_ok(runtime_root=rt)
        if not ok_env:
            _print_json({"ok": False, "blocked": True, "reason": "live_trading_env_forbidden", "detail": why_env})
            return 11

    if args.cmd == "tick":
        if args.role == "ops":
            _print_json(tick_ops_once(runtime_root=rt))
            return 0
        _print_json(tick_research_once(runtime_root=rt, skip_models=bool(args.skip_models)))
        return 0

    if args.cmd == "supervisor-once":
        out = run_role_supervisor_once(
            role=args.role,
            runtime_root=rt,
            skip_models=bool(getattr(args, "skip_models", False)),
            force_all_due=bool(getattr(args, "force_all_due", False)),
        )
        _print_json(out)
        return 0 if out.get("ok") else 1

    if args.cmd == "write-now-live-artifacts":
        from trading_ai.runtime.now_live_proof import write_now_live_autonomy_artifacts

        out = write_now_live_autonomy_artifacts(runtime_root=rt)
        _print_json(out)
        return 0 if out.get("ok") else 2

    if args.cmd == "live-guard-proof":
        from trading_ai.runtime.now_live_proof import run_authoritative_live_guard_proof

        ok, proof = run_authoritative_live_guard_proof(runtime_root=rt)
        _print_json(proof)
        return 0 if ok else 3

    if args.cmd == "accelerated-sim":
        out = _run_accelerated_sim(cycles=int(args.cycles), runtime_root=rt, skip_models=bool(args.skip_models))
        _print_json(out)
        return 0 if out.get("ok") else 4

    if args.cmd == "role-lock-smoke":
        a_ok, a_why, _ = try_acquire_role_lock(role=args.role, holder_id="holder_a", runtime_root=rt, ttl_seconds=60.0)
        b_ok, b_why, _ = try_acquire_role_lock(role=args.role, holder_id="holder_b", runtime_root=rt, ttl_seconds=60.0)
        release_role_lock(role=args.role, holder_id="holder_a", runtime_root=rt)
        out = {"a_ok": a_ok, "a_why": a_why, "b_ok": b_ok, "b_why": b_why, "collision_prevented": bool(a_ok and not b_ok)}
        _print_json(out)
        return 0 if out["collision_prevented"] else 5

    if args.cmd == "autonomy-deploy-preflight":
        from trading_ai.runtime.service_contract_verify import run_autonomy_deploy_preflight

        ok, out = run_autonomy_deploy_preflight(runtime_root=rt)
        _print_json(out)
        return 0 if ok else 6

    if args.cmd == "systemd-unit-contract-verify":
        from trading_ai.runtime.service_contract_verify import verify_systemd_unit_contract_templates

        _repo_root = Path(__file__).resolve().parents[3]
        ok, out = verify_systemd_unit_contract_templates(repo_root=_repo_root)
        _print_json(out)
        return 0 if ok else 7

    if args.cmd == "first-60-live-ops-tick":
        from trading_ai.control.first_60_day_ops import run_first_60_live_ops_tick

        out = run_first_60_live_ops_tick(runtime_root=rt, force=bool(getattr(args, "force", False)))
        _print_json(out)
        return 0 if out.get("ok") else 8

    if args.cmd == "first-60-live-ops-daemon":
        from trading_ai.control.first_60_day_ops import run_first_60_live_ops_daemon_forever

        run_first_60_live_ops_daemon_forever(runtime_root=rt, interval_sec=getattr(args, "interval_sec", None))
        return 0

    holder = args.holder_id or f"pid_{os.getpid()}"
    ok, why, _lock = try_acquire_role_lock(
        role=args.role,
        holder_id=holder,
        runtime_root=rt,
        ttl_seconds=max(30.0, float(args.interval_sec) * 3),
    )
    if not ok:
        _print_json({"ok": False, "blocked": True, "reason": why})
        return 2
    try:
        n = 0
        while True:
            out = run_role_supervisor_once(
                role=args.role,
                runtime_root=rt,
                skip_models=bool(getattr(args, "skip_models", False)),
                force_all_due=bool(args.force_all_due),
            )
            _print_json(out)
            n += 1
            if int(args.cycles or 0) > 0 and n >= int(args.cycles):
                return 0
            time.sleep(max(0.1, float(args.interval_sec)))
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            release_role_lock(role=args.role, holder_id=holder, runtime_root=rt)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
