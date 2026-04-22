#!/usr/bin/env python3
"""
Production host proof: systemd active, /opt layout, deployed smoke, forced supervisors, master smoke.

Writes: <runtime_root>/data/control/production_stack_proof.json
Exit 0 only when the stack is healthy (non-live smokes by default; no venue orders).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _systemctl_active(units: List[str]) -> Tuple[bool, Dict[str, Any]]:
    out: Dict[str, Any] = {}
    ok_all = True
    for u in units:
        cp = subprocess.run(
            ["systemctl", "is-active", u],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        st = (cp.stdout or "").strip()
        row_ok = cp.returncode == 0 and st == "active"
        ok_all = ok_all and row_ok
        out[u] = {"active": row_ok, "status": st, "returncode": cp.returncode}
    return ok_all, out


def _run(
    argv: List[str],
    *,
    env: Dict[str, str],
    cwd: Path,
    timeout: int = 600,
) -> Tuple[int, str, str]:
    cp = subprocess.run(
        argv,
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return cp.returncode, cp.stdout or "", cp.stderr or ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Production stack proof (run on server).")
    ap.add_argument("--public-root", default="/opt/ezra-public")
    ap.add_argument("--private-root", default="/opt/ezra-private")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    ap.add_argument("--venv-root", default="/opt/ezra-venv")
    ap.add_argument("--skip-systemd", action="store_true", help="Skip systemctl checks (dev laptops).")
    args = ap.parse_args()

    public = Path(args.public_root).resolve()
    private = Path(args.private_root).resolve()
    runtime = Path(args.runtime_root).resolve()
    venv = Path(args.venv_root).resolve()
    py = venv / "bin" / "python"
    repo = public / "trading-ai"

    reasons: List[str] = []
    if not py.is_file():
        reasons.append("missing_venv_python")
    if not (repo / "src" / "trading_ai").is_dir():
        reasons.append("missing_public_trading_ai_src")
    if not (private / "trading-ai" / "src" / "trading_ai").is_dir():
        reasons.append("missing_private_trading_ai_src")

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{private / 'trading-ai' / 'src'}:{public / 'trading-ai' / 'src'}"
    env["EZRAS_RUNTIME_ROOT"] = str(runtime)
    env.setdefault("NTE_EXECUTION_MODE", "paper")
    env.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    env.setdefault("COINBASE_EXECUTION_ENABLED", "false")

    systemd_ok = True
    systemd_detail: Dict[str, Any] = {}
    if not args.skip_systemd:
        # Role daemons are authoritative; target may be inactive if only services were started directly.
        systemd_ok, systemd_detail = _systemctl_active(["ezra-ops.service", "ezra-research.service"])
        if not systemd_ok:
            reasons.append("systemd_units_not_active")

    deployed_rc = 99
    deployed_out = ""
    master_rc = 99
    master_out = ""
    sup_ops: Dict[str, Any] = {}
    sup_rs: Dict[str, Any] = {}

    if not reasons:
        smoke_py = repo / "scripts" / "server" / "deployed_environment_smoke.py"
        deployed_rc, deployed_out, _ = _run(
            [str(py), str(smoke_py), "--public-root", str(public), "--private-root", str(private), "--runtime-root", str(runtime), "--venv-root", str(venv)],
            env=env,
            cwd=repo,
        )
        if deployed_rc != 0:
            reasons.append(f"deployed_environment_smoke_exit_{deployed_rc}")

    if not reasons and py.is_file():
        enable = repo / "scripts" / "server" / "enable_full_autonomy_active_live.py"
        rc_e, _, err_e = _run(
            [str(py), str(enable), "--runtime-root", str(runtime), "--artifacts-only", "--reason=production_stack_proof"],
            env=env,
            cwd=repo,
            timeout=120,
        )
        if rc_e != 0:
            reasons.append(f"enable_full_autonomy_active_artifacts_exit_{rc_e}:{err_e[:200]}")

    if not reasons and py.is_file():
        rc1, o1, e1 = _run(
            [
                str(py),
                "-m",
                "trading_ai.runtime",
                "supervisor-once",
                "--role",
                "ops",
                "--runtime-root",
                str(runtime),
                "--force-all-due",
            ],
            env=env,
            cwd=repo,
        )
        try:
            sup_ops = json.loads(o1) if o1.strip().startswith("{") else {"raw": o1[:500], "stderr": e1[:500]}
        except json.JSONDecodeError:
            sup_ops = {"parse_error": True, "stdout_head": o1[:400]}
        if rc1 != 0 or not sup_ops.get("ok"):
            reasons.append(f"supervisor_ops_failed_{rc1}")

        rc2, o2, e2 = _run(
            [
                str(py),
                "-m",
                "trading_ai.runtime",
                "supervisor-once",
                "--role",
                "research",
                "--runtime-root",
                str(runtime),
                "--skip-models",
                "--force-all-due",
            ],
            env=env,
            cwd=repo,
        )
        try:
            sup_rs = json.loads(o2) if o2.strip().startswith("{") else {"raw": o2[:500], "stderr": e2[:500]}
        except json.JSONDecodeError:
            sup_rs = {"parse_error": True, "stdout_head": o2[:400]}
        if rc2 != 0 or not sup_rs.get("ok"):
            reasons.append(f"supervisor_research_failed_{rc2}")

    if not reasons and py.is_file():
        mst = repo / "scripts" / "master_smoke_test.py"
        master_rc, master_out, _ = _run(
            [str(py), str(mst), "--runtime-root", str(runtime), "--cycles", "8"],
            env=env,
            cwd=repo,
            timeout=600,
        )
        if master_rc != 0:
            reasons.append(f"master_smoke_exit_{master_rc}")

    loop_ops = runtime / "data" / "control" / "operating_system" / "loop_status_ops.json"
    loop_rs = runtime / "data" / "control" / "operating_system" / "loop_status_research.json"
    if loop_ops.is_file():
        try:
            d = json.loads(loop_ops.read_text(encoding="utf-8"))
            loops = d.get("loops") if isinstance(d.get("loops"), dict) else {}
            ran = d.get("ran") if isinstance(d.get("ran"), list) else []
            if len(loops) == 0 and len(ran) == 0:
                reasons.append("loop_status_ops_empty")
        except Exception:
            reasons.append("loop_status_ops_unreadable")
    else:
        reasons.append("missing_loop_status_ops")

    if loop_rs.is_file():
        try:
            d = json.loads(loop_rs.read_text(encoding="utf-8"))
            loops = d.get("loops") if isinstance(d.get("loops"), dict) else {}
            ran = d.get("ran") if isinstance(d.get("ran"), list) else []
            if len(loops) == 0 and len(ran) == 0:
                reasons.append("loop_status_research_empty")
        except Exception:
            reasons.append("loop_status_research_unreadable")
    else:
        reasons.append("missing_loop_status_research")

    ok = not reasons
    report: Dict[str, Any] = {
        "truth_version": "production_stack_proof_v1",
        "generated_at": _iso(),
        "ok": ok,
        "runtime_root": str(runtime),
        "fail_reasons": reasons,
        "systemd": {"skipped": bool(args.skip_systemd), "ok": systemd_ok, "detail": systemd_detail},
        "deployed_environment_smoke": {"returncode": deployed_rc, "stdout_tail": deployed_out[-1200:]},
        "master_smoke": {"returncode": master_rc, "stdout_tail": master_out[-1200:]},
        "supervisor_ops": sup_ops,
        "supervisor_research": sup_rs,
        "honesty": "Proof uses paper/non-live process env; live venue orders are not asserted here.",
    }
    out_path = runtime / "data" / "control" / "production_stack_proof.json"
    _write_json(out_path, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
