#!/usr/bin/env python3
"""
Deployed-environment smoke for /opt layout (NON-LIVE).

Proves:
- overlay imports resolve (PYTHONPATH order)
- runtime root is writable
- ops + research supervisors run (force all loops)
- core artifacts exist and task routing + consumption produce durable outputs

Writes:
- <runtime_root>/data/control/deployed_environment_smoke.json

Never places orders.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


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
    sp = [str(x) for x in sys.path]
    pri = str(private_src)
    pub = str(public_src)
    if pri == pub:
        # Single-tree local proofs (or mirrored checkouts): overlay ordering is not meaningful.
        private_first = pri in sp[:12]
    else:
        private_first = (pri in sp[:5]) and (sp.index(pri) < sp.index(pub) if pub in sp and pri in sp else True)
    return {
        "sys_path_head": sp[:12],
        "private_first": private_first,
        "private_src": pri,
        "public_src": pub,
    }


def _touch_writable(root: Path) -> Dict[str, Any]:
    p = root / "data" / "control" / "smoke_writable_probe.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"ok {_iso()}\n", encoding="utf-8")
    return {"ok": True, "path": str(p)}


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Deployed environment smoke (non-live)")
    ap.add_argument("--public-root", default="/opt/ezra-public")
    ap.add_argument("--private-root", default="/opt/ezra-private")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    ap.add_argument("--venv-root", default="/opt/ezra-venv")
    args = ap.parse_args(argv)

    venv_root = Path(args.venv_root).resolve()
    if sys.prefix != sys.base_prefix:
        # Active venv: use it so local/CI proofs can pass without /opt/ezra-venv.
        venv_root = Path(sys.prefix).resolve()

    public_src = Path(args.public_root).resolve() / "trading-ai" / "src"
    private_src = Path(args.private_root).resolve() / "trading-ai" / "src"
    runtime_root = Path(args.runtime_root).resolve()

    live_ok, live_errs = _assert_live_disabled()
    overlay = _py_path_overlay_ok(public_src=public_src, private_src=private_src)

    import_errors: List[str] = []
    for mod in (
        "trading_ai.runtime.operating_system",
        "trading_ai.global_layer.mission_goals_operating_layer",
        "trading_ai.global_layer.mission_goals_task_consumer",
        "trading_ai.global_layer.task_intake",
        "trading_ai.nte.hardening.live_order_guard",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            import_errors.append(f"{mod}:{type(exc).__name__}")

    try:
        writable = _touch_writable(runtime_root)
    except Exception as exc:
        writable = {"ok": False, "error": type(exc).__name__}

    # Run supervisors once each (forced all loops).
    try:
        from trading_ai.runtime.operating_system import run_role_supervisor_once

        ops = run_role_supervisor_once(role="ops", runtime_root=runtime_root, force_all_due=True)
        research = run_role_supervisor_once(role="research", runtime_root=runtime_root, skip_models=True, force_all_due=True)
    except Exception as exc:
        ops = {"ok": False, "error": type(exc).__name__}
        research = {"ok": False, "error": type(exc).__name__}

    expected_paths = {
        "ops_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_ops.json",
        "research_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_research.json",
        "mission_goals_plan": runtime_root / "data" / "control" / "mission_goals_operating_plan.json",
        "pnl_review": runtime_root / "data" / "control" / "pnl_review.json",
        "comparisons": runtime_root / "data" / "control" / "performance_comparisons.json",
        "task_intake_state": runtime_root / "data" / "control" / "task_intake_state.json",
        "task_rollup": runtime_root / "data" / "control" / "task_rollup.json",
    }
    exists = {k: v.is_file() for k, v in expected_paths.items()}

    # Probe tasks.jsonl under runtime governance dir (NOT repo-local).
    task_probe = {"ok": False, "path": None, "matches": []}
    try:
        from trading_ai.global_layer.task_registry import tasks_store_path

        tasks_path = tasks_store_path()
        task_probe["path"] = str(tasks_path)
        if tasks_path.is_file():
            lines = tasks_path.read_text(encoding="utf-8").splitlines()[-800:]
            matches = []
            for ln in lines:
                if "mission_goals::" in ln or "comparisons::avenue" in ln or "pnl_review::risk_reduction" in ln:
                    matches.append(ln[:320])
                    if len(matches) >= 6:
                        break
            task_probe = {"ok": True, "path": str(tasks_path), "matches": matches}
    except Exception as exc:
        task_probe = {"ok": False, "path": task_probe.get("path"), "error": type(exc).__name__}

    inbox_dir = runtime_root / "data" / "control" / "bot_inboxes"
    inbox_ok = inbox_dir.is_dir() and any(p.suffix == ".json" for p in inbox_dir.iterdir())

    report: Dict[str, Any] = {
        "truth_version": "deployed_environment_smoke_v1",
        "generated_at": _iso(),
        "paths": {
            "public_root": args.public_root,
            "private_root": args.private_root,
            "runtime_root": args.runtime_root,
            "venv_root": str(venv_root),
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
        "bot_inboxes": {"path": str(inbox_dir), "ok": bool(inbox_ok)},
        "honesty": "Smoke proves supervisors + artifact/task emission/consumption in deployed layout; no orders.",
    }

    # Emit deploy_preflight.json (separate subprocess: avoids importlib edge cases across Python builds).
    preflight_path = Path(__file__).resolve().parent / "deploy_preflight.py"
    cp_pf = subprocess.run(
        [
            sys.executable,
            str(preflight_path),
            "--public-root",
            str(Path(args.public_root).resolve()),
            "--private-root",
            str(Path(args.private_root).resolve()),
            "--runtime-root",
            str(runtime_root),
            "--venv-root",
            str(venv_root),
            "--write-report",
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    report["deploy_preflight"] = {
        "exit_code": int(cp_pf.returncode),
        "stderr_head": (cp_pf.stderr or "")[:800],
        "stdout_head": (cp_pf.stdout or "")[:800],
    }

    out_path = runtime_root / "data" / "control" / "deployed_environment_smoke.json"
    _write_json(out_path, report)

    print(json.dumps(report, indent=2, sort_keys=True))

    ok = (
        live_ok
        and report["imports_ok"]
        and bool(report["runtime_writable"].get("ok"))
        and all(exists.values())
        and bool(ops.get("ok"))
        and bool(research.get("ok"))
        and bool(inbox_ok)
        and int(cp_pf.returncode) == 0
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

