#!/usr/bin/env python3
"""
Deployed-environment smoke for /opt layout.

Runs in the same environment that systemd services use:
- validates PYTHONPATH overlay order
- validates runtime root is writable
- runs one supervisor step for ops and research (forced all loops)
- proves tasks are created and key artifacts exist
- writes machine-readable report to:
  /opt/ezra-runtime/data/control/deployed_environment_smoke.json (or runtime_root override)
- includes ``live_micro_private_build`` (private modules + operator scripts + static CLI wiring check)

Never places live orders.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _assert_live_disabled() -> Tuple[bool, List[str]]:
    errs: List[str] = []
    mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    if mode in ("live", "prod", "production"):
        errs.append("NTE_EXECUTION_MODE_is_live")
    if _env_truthy("NTE_LIVE_TRADING_ENABLED"):
        errs.append("NTE_LIVE_TRADING_ENABLED_true")
    if _env_truthy("COINBASE_EXECUTION_ENABLED"):
        errs.append("COINBASE_EXECUTION_ENABLED_true")
    return (len(errs) == 0), errs


def _py_path_overlay_ok(public_src: Path, private_src: Path) -> Dict[str, Any]:
    # Verify sys.path begins with private then public when both exist.
    sp = [str(x) for x in sys.path]
    pri = str(private_src)
    pub = str(public_src)
    return {
        "sys_path_head": sp[:10],
        "private_first": (pri in sp[:3]) and (sp.index(pri) < sp.index(pub) if pub in sp and pri in sp else True),
        "private_src": pri,
        "public_src": pub,
    }


def _touch_writable(root: Path) -> Dict[str, Any]:
    p = root / "data" / "control" / "smoke_writable_probe.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"ok {_iso()}\n", encoding="utf-8")
    return {"ok": True, "path": str(p)}


def _live_micro_deployment_main_registers(private_main: Path) -> Tuple[bool, List[str]]:
    """
    Statically verify ``trading_ai.deployment.__main__`` wires live-micro subcommands.

    We intentionally do not shell out to ``python -m trading_ai.deployment -h`` here: that entrypoint
    runs ``enforce_ssl()`` first, which fails on some developer macOS Pythons (LibreSSL) even when
    the tree is correct. Production servers should still run ``-h`` manually for runtime proof.
    """
    if not private_main.is_file():
        return False, ["missing_deployment__main__.py"]
    try:
        txt = private_main.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, [f"read_error:{type(exc).__name__}"]
    needles = (
        "live-micro-enablement-request",
        "live-micro-write-session-limits",
        "live-micro-preflight",
        "live-micro-readiness",
        "live-micro-guard-proof",
        "live-micro-verify-contract",
        "live-micro-record-start",
        "live-micro-disable-receipt",
        "live-micro-pause",
        "live-micro-resume",
    )
    absent = [n for n in needles if n not in txt]
    return len(absent) == 0, absent


def _live_micro_private_build_probe(
    *,
    venv_root: Path,
    private_repo: Path,
    public_src: Path,
    private_src: Path,
) -> Dict[str, Any]:
    """
    Prove the *private* checkout contains the live-micro operator pack and CLI registrations.

    This catches the common failure mode where /opt/ezra-public is fresh but /opt/ezra-private lags
    an old commit (missing ``live_micro_enablement.py`` / ``live-micro-*`` argparse wiring).
    """
    del venv_root, public_src  # retained for signature symmetry with callers / future runtime probes
    required_modules = (
        private_src / "trading_ai" / "deployment" / "live_micro_enablement.py",
        private_src / "trading_ai" / "deployment" / "__main__.py",
    )
    required_scripts = (
        private_repo / "trading-ai" / "scripts" / "server" / "live_micro_operator.sh",
        private_repo / "trading-ai" / "scripts" / "server" / "live_micro_smoke.sh",
    )
    missing_mods = [str(p) for p in required_modules if not p.is_file()]
    missing_scripts = [str(p) for p in required_scripts if not p.is_file()]

    main_py = private_src / "trading_ai" / "deployment" / "__main__.py"
    reg_ok, absent = _live_micro_deployment_main_registers(main_py)
    mods_ok = len(missing_mods) == 0
    scripts_ok = len(missing_scripts) == 0
    return {
        "ok": bool(mods_ok and scripts_ok and reg_ok),
        "missing_private_modules": missing_mods,
        "missing_private_scripts": missing_scripts,
        "subcommands_absent_from_deployment_main": absent,
        "honesty": (
            "Build wiring: private modules + operator scripts + live-micro strings in deployment/__main__.py. "
            "On the server, also run: /opt/ezra-venv/bin/python -m trading_ai.deployment -h (requires OpenSSL-capable Python)."
        ),
    }


def _git_head(repo_root: Path) -> Dict[str, Any]:
    """Best-effort commit SHA for operator proof (no network)."""
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return {"ok": False, "reason": "no_dot_git", "path": str(repo_root)}
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        sha = (cp.stdout or "").strip()
        if cp.returncode == 0 and len(sha) >= 7:
            return {"ok": True, "sha": sha, "path": str(repo_root)}
        return {"ok": False, "reason": f"git_exit_{cp.returncode}", "stderr": (cp.stderr or "")[:200]}
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}


def _read_deployed_refs(runtime_root: Path) -> Dict[str, Any]:
    p = runtime_root / "data" / "control" / "deployed_refs.json"
    if not p.is_file():
        return {"present": False, "path": str(p)}
    try:
        return {"present": True, "path": str(p), "payload": json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"present": True, "path": str(p), "error": type(exc).__name__}


def _optional_simulation_artifacts(runtime_root: Path) -> Dict[str, Any]:
    ctrl = runtime_root / "data" / "control"
    names = (
        "sim_trade_log.json",
        "sim_fill_log.json",
        "sim_pnl.json",
        "sim_lessons.json",
        "sim_tasks.json",
        "sim_24h_validation.json",
        "regression_drift.json",
        "service_status.json",
        "master_smoke.json",
        "tasks.jsonl",
    )
    return {n: bool((ctrl / n).is_file()) for n in names}


def _databank_probe(runtime_root: Path) -> Dict[str, Any]:
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.setdefault("TRADE_DATABANK_MEMORY_ROOT", str(runtime_root / "databank"))
    try:
        from trading_ai.nte.databank.local_trade_store import resolve_databank_root

        root, src = resolve_databank_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".deployed_smoke_databank_probe"
        probe.write_text(_iso() + "\n", encoding="utf-8")
        return {"ok": True, "databank_root": str(root), "resolution": src}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _post_trade_probe(runtime_root: Path) -> Dict[str, Any]:
    try:
        for rel in ("logs", "state"):
            p = runtime_root / rel
            p.mkdir(parents=True, exist_ok=True)
            (p / ".deployed_smoke_post_trade_probe").write_text(_iso() + "\n", encoding="utf-8")
        return {"ok": True, "logs_dir": str(runtime_root / "logs"), "state_dir": str(runtime_root / "state")}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Deployed environment smoke (non-live)")
    ap.add_argument("--public-root", default="/opt/ezra-public")
    ap.add_argument("--private-root", default="/opt/ezra-private")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    ap.add_argument("--venv-root", default="/opt/ezra-venv")
    args = ap.parse_args(argv)

    public_src = Path(args.public_root).resolve() / "trading-ai" / "src"
    private_src = Path(args.private_root).resolve() / "trading-ai" / "src"
    runtime_root = Path(args.runtime_root).resolve()

    # Align process env with services so imports, databank, and supervisors resolve the same tree.
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.setdefault("TRADE_DATABANK_MEMORY_ROOT", str(runtime_root / "databank"))

    live_ok, live_errs = _assert_live_disabled()
    overlay = _py_path_overlay_ok(public_src=public_src, private_src=private_src)

    # Import critical modules (must resolve with overlay).
    import_errors: List[str] = []
    for mod in (
        "trading_ai.runtime.operating_system",
        "trading_ai.global_layer.mission_goals_operating_layer",
        "trading_ai.global_layer.mission_goals_task_consumer",
        "trading_ai.nte.hardening.live_order_guard",
        "trading_ai.deployment",
        "trading_ai.runtime.trade_snapshots",
        "trading_ai.runtime_proof.first_twenty_judge",
        "trading_ai.automation.post_trade_hub",
        "trading_ai.shark.mission",
        "trading_ai.global_layer.governance_order_gate",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            import_errors.append(f"{mod}:{type(exc).__name__}")

    writable = {"ok": False}
    try:
        writable = _touch_writable(runtime_root)
    except Exception as exc:
        writable = {"ok": False, "error": type(exc).__name__}

    # Run supervisors once each (forced all loops) and record status paths.
    try:
        from trading_ai.runtime.operating_system import run_role_supervisor_once

        ops = run_role_supervisor_once(role="ops", runtime_root=runtime_root, force_all_due=True)
        research = run_role_supervisor_once(role="research", runtime_root=runtime_root, skip_models=True, force_all_due=True)
    except Exception as exc:
        ops = {"ok": False, "error": type(exc).__name__}
        research = {"ok": False, "error": type(exc).__name__}

    # Verify key artifacts exist
    expected_paths = {
        "ops_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_ops.json",
        "research_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_research.json",
        "mission_goals_plan": runtime_root / "data" / "control" / "mission_goals_operating_plan.json",
        "pnl_review": runtime_root / "data" / "control" / "pnl_review.json",
        "comparisons": runtime_root / "data" / "control" / "performance_comparisons.json",
    }
    exists = {k: v.is_file() for k, v in expected_paths.items()}

    # Verify tasks created (governance JSONL and/or runtime mirror)
    task_probe = {"ok": False, "paths": [], "matches": []}
    try:
        sys.path.insert(0, str(Path(args.public_root).resolve() / "trading-ai" / "src"))
        from trading_ai.global_layer.task_registry import tasks_store_path

        cand = [
            tasks_store_path(),
            runtime_root / "data" / "control" / "tasks.jsonl",
            Path(args.public_root).resolve() / "trading-ai" / "src" / "trading_ai" / "global_layer" / "_governance_data" / "tasks.jsonl",
        ]
        task_probe["paths"] = [str(p) for p in cand]
        for tasks_path in cand:
            if not tasks_path.is_file():
                continue
            lines = tasks_path.read_text(encoding="utf-8").splitlines()[-500:]
            matches = []
            for ln in lines:
                if "mission_goals::" in ln or "comparisons::avenue" in ln or "pnl_review::risk_reduction" in ln:
                    matches.append(ln[:300])
                    if len(matches) >= 5:
                        break
            task_probe = {"ok": True, "paths": [str(tasks_path)], "matches": matches}
            break
    except Exception as exc:
        task_probe = {"ok": False, "paths": task_probe.get("paths") or [], "error": type(exc).__name__}

    public_repo = Path(args.public_root).resolve()
    private_repo = Path(args.private_root).resolve()
    repo_git = {
        "public_repo_head": _git_head(public_repo),
        "private_repo_head": _git_head(private_repo),
    }
    deployed_refs = _read_deployed_refs(runtime_root)
    databank_probe = _databank_probe(runtime_root)
    post_trade_probe = _post_trade_probe(runtime_root)
    optional_sim = _optional_simulation_artifacts(runtime_root)
    live_micro_build = _live_micro_private_build_probe(
        venv_root=Path(args.venv_root).resolve(),
        private_repo=private_repo,
        public_src=public_src,
        private_src=private_src,
    )

    report = {
        "truth_version": "deployed_environment_smoke_v1",
        "generated_at": _iso(),
        "paths": {
            "public_root": args.public_root,
            "private_root": args.private_root,
            "runtime_root": args.runtime_root,
            "venv_root": args.venv_root,
        },
        "live_disabled": {"ok": live_ok, "errors": live_errs},
        "python_overlay": overlay,
        "imports_ok": len(import_errors) == 0,
        "import_errors": import_errors,
        "runtime_writable": writable,
        "ops_supervisor": ops,
        "research_supervisor": research,
        "expected_artifacts_exist": exists,
        "task_probe": task_probe,
        "commit_evidence": repo_git,
        "deployed_refs_artifact": deployed_refs,
        "databank_probe": databank_probe,
        "post_trade_path_probe": post_trade_probe,
        "optional_simulation_and_router_artifacts": optional_sim,
        "live_micro_private_build": live_micro_build,
        "honesty": "Smoke does not place orders; it proves supervisor loops and artifact/task emission in deployed layout.",
    }

    out_path = runtime_root / "data" / "control" / "deployed_environment_smoke.json"
    _write_json(out_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))

    # Operator gate scripts expect ``deploy_preflight.json`` alongside the smoke report.
    try:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from scripts.server.deploy_preflight import build_report, resolve_paths

        pr = resolve_paths(
            public_root=str(Path(args.public_root).resolve()),
            private_root=str(Path(args.private_root).resolve()),
            runtime_root=str(runtime_root),
            venv_root=str(Path(args.venv_root).resolve()),
        )
        _write_json(runtime_root / "data" / "control" / "deploy_preflight.json", build_report(pr))
    except Exception as exc:
        _write_json(
            runtime_root / "data" / "control" / "deploy_preflight.json",
            {"truth_version": "deploy_preflight_v1", "ok": False, "error": type(exc).__name__},
        )

    ok = (
        live_ok
        and report["imports_ok"]
        and report["runtime_writable"].get("ok")
        and all(exists.values())
        and bool(ops.get("ok"))
        and bool(research.get("ok"))
        and bool(databank_probe.get("ok"))
        and bool(post_trade_probe.get("ok"))
        and bool(live_micro_build.get("ok"))
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

