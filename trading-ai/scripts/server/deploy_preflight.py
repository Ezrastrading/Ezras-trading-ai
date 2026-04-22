#!/usr/bin/env python3
"""
Deployment preflight for dual-repo server layout.

Goal: fail-closed before any service restart.
Does not require secrets; never prints secret values.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import server_sys_path


@dataclass(frozen=True)
class Paths:
    public_root: Path
    private_root: Path
    runtime_root: Path
    venv_root: Path


def _p(s: str) -> Path:
    return Path(s).expanduser().resolve()


def resolve_paths(
    *,
    public_root: str = "/opt/ezra-public",
    private_root: str = "/opt/ezra-private",
    runtime_root: str = "/opt/ezra-runtime",
    venv_root: str = "/opt/ezra-venv",
) -> Paths:
    return Paths(
        public_root=_p(public_root),
        private_root=_p(private_root),
        runtime_root=_p(runtime_root),
        venv_root=_p(venv_root),
    )


def _truth(ok: bool, *, reason: str = "", detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": bool(ok), "reason": reason, "detail": detail or {}}


def _env_bool(name: str) -> Optional[bool]:
    if name not in os.environ:
        return None
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def assert_live_disabled() -> Tuple[bool, List[str]]:
    """
    Hard deployment contract: live trading must be disabled for switch-ready mode.
    """
    errs: List[str] = []
    mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    live_flag = _env_bool("NTE_LIVE_TRADING_ENABLED")
    cb_exec = _env_bool("COINBASE_EXECUTION_ENABLED")
    if mode in ("live", "prod", "production"):
        errs.append("NTE_EXECUTION_MODE_is_live")
    if live_flag is True:
        errs.append("NTE_LIVE_TRADING_ENABLED_true")
    if cb_exec is True:
        errs.append("COINBASE_EXECUTION_ENABLED_true")
    return (len(errs) == 0), errs


def critical_imports_ok() -> Tuple[bool, List[str]]:
    errs: List[str] = []
    try:
        import trading_ai  # noqa: F401
    except Exception as exc:
        errs.append(f"import_trading_ai_failed:{type(exc).__name__}")
    for mod in (
        "trading_ai.runtime.operating_system",
        "trading_ai.nte.hardening.live_order_guard",
        "trading_ai.global_layer.mission_goals_operating_layer",
        "trading_ai.global_layer.mission_goals_task_consumer",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            errs.append(f"import_failed:{mod}:{type(exc).__name__}")
    return (len(errs) == 0), errs


def verify_filesystem(p: Paths) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    checks["public_repo"] = _truth((p.public_root / "trading-ai").is_dir(), reason="public_repo_dir_missing")
    checks["private_repo"] = _truth((p.private_root / "trading-ai").is_dir(), reason="private_repo_dir_missing")
    checks["venv_python"] = _truth((p.venv_root / "bin" / "python").is_file(), reason="venv_python_missing")
    checks["runtime_root"] = _truth(p.runtime_root.is_dir(), reason="runtime_root_missing")

    env_dir = p.runtime_root / "env"
    checks["env_dir"] = _truth(env_dir.is_dir(), reason="env_dir_missing")
    checks["env_common"] = _truth((env_dir / "common.env").is_file(), reason="common_env_missing")
    # Optional:
    checks["env_ops_optional"] = _truth(True, reason="", detail={"path": str(env_dir / "ops.env")})
    checks["env_research_optional"] = _truth(True, reason="", detail={"path": str(env_dir / "research.env")})
    return checks


def verify_systemd_unit_templates(p: Paths) -> Dict[str, Any]:
    unit_dir = p.public_root / "trading-ai" / "docs" / "systemd"
    return {
        "unit_dir_present": _truth(unit_dir.is_dir(), reason="unit_dir_missing", detail={"path": str(unit_dir)}),
        "ops_unit_present": _truth((unit_dir / "ezra-ops.service").is_file(), reason="ops_unit_missing"),
        "research_unit_present": _truth((unit_dir / "ezra-research.service").is_file(), reason="research_unit_missing"),
    }


def build_report(p: Paths) -> Dict[str, Any]:
    live_ok, live_errs = assert_live_disabled()
    imp_ok, imp_errs = critical_imports_ok()
    fs = verify_filesystem(p)
    units = verify_systemd_unit_templates(p)

    ok = live_ok and imp_ok and all(v.get("ok") for v in fs.values()) and all(v.get("ok") for v in units.values())

    return {
        "truth_version": "deploy_preflight_v1",
        "ok": bool(ok),
        "paths": {
            "public_root": str(p.public_root),
            "private_root": str(p.private_root),
            "runtime_root": str(p.runtime_root),
            "venv_root": str(p.venv_root),
        },
        "checks": {
            "live_disabled": {"ok": live_ok, "errors": live_errs},
            "critical_imports": {"ok": imp_ok, "errors": imp_errs},
            "filesystem": fs,
            "systemd_unit_templates": units,
        },
        "honesty": "This preflight does not validate secrets; it validates switch-ready non-live posture and runtime wiring.",
    }


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Ezra dual-repo deploy preflight (fail-closed)")
    ap.add_argument("--public-root", default="/opt/ezra-public")
    ap.add_argument("--private-root", default="/opt/ezra-private")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    ap.add_argument("--venv-root", default="/opt/ezra-venv")
    ap.add_argument("--write-report", action="store_true", help="Write report to runtime_root/data/control/deploy_preflight.json")
    args = ap.parse_args(argv)

    p = resolve_paths(
        public_root=args.public_root,
        private_root=args.private_root,
        runtime_root=args.runtime_root,
        venv_root=args.venv_root,
    )
    server_sys_path.ensure_dual_repo_src_on_path(public_root=p.public_root, private_root=p.private_root)
    rep = build_report(p)
    print(json.dumps(rep, indent=2, sort_keys=True))
    if args.write_report:
        out = p.runtime_root / "data" / "control" / "deploy_preflight.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

