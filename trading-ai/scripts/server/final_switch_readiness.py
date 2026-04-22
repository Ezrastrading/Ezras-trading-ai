#!/usr/bin/env python3
"""
Final switch readiness report (NON-LIVE).

This is a conservative gate that aggregates:
- deploy preflight
- deployed environment smoke
- micro trade readiness
- operating system loop status freshness (best-effort)

Writes:
- <runtime_root>/data/control/final_switch_readiness.json

Never places orders. Does not flip any switches.
"""

from __future__ import annotations

import json
import os
import sys
import time
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


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


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


def _freshness(path: Path, *, max_age_sec: float) -> Dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "reason": "missing", "path": str(path)}
    try:
        age = time.time() - path.stat().st_mtime
    except Exception:
        return {"ok": False, "reason": "stat_failed", "path": str(path)}
    return {"ok": age <= max_age_sec, "age_sec": round(age, 3), "max_age_sec": max_age_sec, "path": str(path)}


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Final switch readiness (non-live)")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    args = ap.parse_args(argv)

    root = Path(args.runtime_root).resolve()
    live_ok, live_errs = _assert_live_disabled()

    preflight_p = root / "data" / "control" / "deploy_preflight.json"
    smoke_p = root / "data" / "control" / "deployed_environment_smoke.json"
    micro_p = root / "data" / "control" / "micro_trade_readiness.json"
    ops_status_p = root / "data" / "control" / "operating_system" / "loop_status_ops.json"
    res_status_p = root / "data" / "control" / "operating_system" / "loop_status_research.json"

    preflight = _read_json(preflight_p)
    smoke = _read_json(smoke_p)
    micro = _read_json(micro_p)

    blockers: List[str] = []
    if not live_ok:
        blockers.extend(live_errs)
    if preflight.get("ok") is not True:
        blockers.append("deploy_preflight_not_ok")
    if smoke.get("live_disabled", {}).get("ok") is not True:
        blockers.append("deployed_environment_smoke_live_disabled_not_ok")
    if smoke.get("imports_ok") is not True:
        blockers.append("deployed_environment_smoke_imports_not_ok")
    lm = smoke.get("live_micro_private_build") if isinstance(smoke, dict) else None
    if not isinstance(lm, dict):
        blockers.append("deployed_environment_smoke_missing_live_micro_private_build")
    elif lm.get("ok") is not True:
        blockers.append("live_micro_private_build_not_ok")
    if smoke.get("expected_artifacts_exist") and any(v is False for v in (smoke.get("expected_artifacts_exist") or {}).values()):
        blockers.append("deployed_environment_smoke_missing_expected_artifacts")
    if micro.get("ok") is not True:
        blockers.append("micro_trade_readiness_not_ok")

    freshness = {
        "ops_loop_status_fresh": _freshness(ops_status_p, max_age_sec=240.0),
        "research_loop_status_fresh": _freshness(res_status_p, max_age_sec=600.0),
    }
    if not freshness["ops_loop_status_fresh"]["ok"]:
        blockers.append("ops_loop_status_not_fresh")
    if not freshness["research_loop_status_fresh"]["ok"]:
        blockers.append("research_loop_status_not_fresh")

    payload: Dict[str, Any] = {
        "truth_version": "final_switch_readiness_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "live_disabled": {"ok": live_ok, "errors": live_errs},
        "inputs": {
            "deploy_preflight_path": str(preflight_p),
            "deployed_environment_smoke_path": str(smoke_p),
            "micro_trade_readiness_path": str(micro_p),
            "ops_loop_status_path": str(ops_status_p),
            "research_loop_status_path": str(res_status_p),
        },
        "freshness": freshness,
        "ok": len(blockers) == 0,
        "blockers": blockers,
        "honesty": "This is conservative. It requires fresh loop status artifacts in addition to gate reports.",
    }

    out = root / "data" / "control" / "final_switch_readiness.json"
    _write_json(out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

