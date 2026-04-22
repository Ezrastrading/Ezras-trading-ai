#!/usr/bin/env python3
"""
Micro-trade readiness gate (NON-LIVE).

Goal: prove the deployed system is autonomous and restart-safe in every non-live dimension,
so the only remaining manual action is intentional live enablement.

Writes machine-readable report to:
- /opt/ezra-runtime/data/control/micro_trade_readiness.json (or --runtime-root override)

Never places orders.
"""

from __future__ import annotations

import json
import os
import shutil
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


def _exists(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "exists": bool(path.is_file())}


def _mtime_age_sec(path: Path) -> Optional[float]:
    if not path.is_file():
        return None
    try:
        import time

        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _systemd_unit_probe(units: Tuple[str, ...]) -> Dict[str, Any]:
    if not shutil.which("systemctl"):
        return {"ok": True, "skipped": True, "reason": "systemctl_not_in_path"}
    out: Dict[str, Any] = {"ok": True, "units": {}}
    for u in units:
        try:
            cp = subprocess.run(
                ["systemctl", "is-active", u],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            active = (cp.stdout or "").strip() == "active"
            out["units"][u] = {"active": active, "stdout": (cp.stdout or "").strip(), "returncode": cp.returncode}
        except Exception as exc:
            out["units"][u] = {"error": type(exc).__name__}
    # Do not fail readiness on systemd when not managing these units (e.g. local dev).
    return out


def _databank_trade_path_ok(runtime_root: Path) -> Dict[str, Any]:
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.setdefault("TRADE_DATABANK_MEMORY_ROOT", str(runtime_root / "databank"))
    try:
        from trading_ai.nte.databank.local_trade_store import global_trade_events_path

        p = global_trade_events_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        probe = p.parent / ".micro_trade_readiness_databank_probe"
        probe.write_text(_iso() + "\n", encoding="utf-8")
        return {"ok": True, "trade_events_path": str(p)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _post_trade_snapshot_import_ok() -> Dict[str, Any]:
    try:
        from trading_ai.runtime.trade_snapshots import SnapshotWriteError, snapshot_trades_master  # noqa: F401

        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _first_twenty_judge_import_ok() -> Dict[str, Any]:
    try:
        from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session  # noqa: F401

        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Micro-trade readiness gate (non-live)")
    ap.add_argument("--runtime-root", default="/opt/ezra-runtime")
    ap.add_argument("--public-root", default="/opt/ezra-public")
    ap.add_argument("--private-root", default="/opt/ezra-private")
    args = ap.parse_args(argv)

    runtime_root = Path(args.runtime_root).resolve()
    public_root = Path(args.public_root).resolve()
    private_root = Path(args.private_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.setdefault("TRADE_DATABANK_MEMORY_ROOT", str(runtime_root / "databank"))

    live_ok, live_errs = _assert_live_disabled()

    # Imports must resolve (overlay proof is separate smoke, but we still do a minimal import set).
    import_errors: List[str] = []
    for mod in (
        "trading_ai.runtime.operating_system",
        "trading_ai.global_layer.task_intake",
        "trading_ai.global_layer.mission_goals_task_consumer",
        "trading_ai.nte.hardening.live_order_guard",
        "trading_ai.deployment",
        "trading_ai.shark.mission",
        "trading_ai.global_layer.governance_order_gate",
        "trading_ai.automation.post_trade_hub",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            import_errors.append(f"{mod}:{type(exc).__name__}")

    # Core autonomy artifacts
    expected = {
        "deploy_preflight": runtime_root / "data" / "control" / "deploy_preflight.json",
        "deployed_environment_smoke": runtime_root / "data" / "control" / "deployed_environment_smoke.json",
        "ops_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_ops.json",
        "research_loop_status": runtime_root / "data" / "control" / "operating_system" / "loop_status_research.json",
        "role_contract": runtime_root / "data" / "control" / "operating_system" / "role_contract.json",
        "mission_goals_plan": runtime_root / "data" / "control" / "mission_goals_operating_plan.json",
        "pnl_review": runtime_root / "data" / "control" / "pnl_review.json",
        "comparisons": runtime_root / "data" / "control" / "performance_comparisons.json",
        "task_rollup": runtime_root / "data" / "control" / "task_rollup.json",
        "task_intake_state": runtime_root / "data" / "control" / "task_intake_state.json",
    }

    exists = {k: _exists(v) for k, v in expected.items()}

    smoke = _read_json(expected["deployed_environment_smoke"])
    preflight = _read_json(expected["deploy_preflight"])

    # Strong invariants (fail-closed)
    blockers: List[str] = []
    if not live_ok:
        blockers.extend(live_errs)
    if import_errors:
        blockers.extend([f"import_failed:{x}" for x in import_errors])
    for k, row in exists.items():
        if not bool(row.get("exists")):
            blockers.append(f"missing_artifact:{k}")

    # Deployed smoke must itself have asserted live disabled ok.
    if isinstance(smoke.get("live_disabled"), dict) and smoke.get("live_disabled", {}).get("ok") is False:
        blockers.append("deployed_environment_smoke_reports_live_not_disabled")
    if smoke.get("imports_ok") is False:
        blockers.append("deployed_environment_smoke_imports_failed")
    lm = smoke.get("live_micro_private_build") if isinstance(smoke, dict) else None
    if not isinstance(lm, dict):
        blockers.append("deployed_environment_smoke_missing_live_micro_private_build")
    elif lm.get("ok") is not True:
        blockers.append("live_micro_private_build_not_ok")
    if isinstance(preflight.get("checks"), dict):
        ld = (preflight.get("checks") or {}).get("live_disabled") or {}
        if isinstance(ld, dict) and ld.get("ok") is False:
            blockers.append("deploy_preflight_live_disabled_failed")

    # Task consumption proof: at least one bot inbox exists (or unassigned bucket) after intake loop.
    inbox_dir = runtime_root / "data" / "control" / "bot_inboxes"
    inbox_ok = inbox_dir.is_dir() and any(p.suffix == ".json" for p in inbox_dir.iterdir())
    if not inbox_ok:
        blockers.append("no_bot_inboxes_written")

    databank_ok = _databank_trade_path_ok(runtime_root)
    if not databank_ok.get("ok"):
        blockers.append(f"databank_trade_path_failed:{databank_ok.get('error')}")

    snap_imp = _post_trade_snapshot_import_ok()
    if not snap_imp.get("ok"):
        blockers.append(f"post_trade_snapshot_import_failed:{snap_imp.get('error')}")

    judge_imp = _first_twenty_judge_import_ok()
    if not judge_imp.get("ok"):
        blockers.append(f"first_twenty_judge_import_failed:{judge_imp.get('error')}")

    smoke_commit = smoke.get("commit_evidence") if isinstance(smoke, dict) else None

    ops_age = _mtime_age_sec(runtime_root / "data" / "control" / "operating_system" / "loop_status_ops.json")
    rs_age = _mtime_age_sec(runtime_root / "data" / "control" / "operating_system" / "loop_status_research.json")
    stale_s = float((os.environ.get("EZRA_READINESS_MAX_LOOP_AGE_SEC") or "604800").strip() or "604800")
    if ops_age is not None and ops_age > stale_s:
        blockers.append("ops_loop_status_stale")
    if rs_age is not None and rs_age > stale_s:
        blockers.append("research_loop_status_stale")

    systemd = _systemd_unit_probe(("ezra-ops.service", "ezra-research.service"))

    payload: Dict[str, Any] = {
        "truth_version": "micro_trade_readiness_v2",
        "generated_at": _iso(),
        "paths": {
            "runtime_root": str(runtime_root),
            "public_root": str(public_root),
            "private_root": str(private_root),
        },
        "live_disabled": {"ok": live_ok, "errors": live_errs},
        "imports_ok": len(import_errors) == 0,
        "import_errors": import_errors,
        "expected_artifacts": exists,
        "bot_inboxes": {"path": str(inbox_dir), "ok": bool(inbox_ok)},
        "databank_trade_path": databank_ok,
        "post_trade_snapshot_module": snap_imp,
        "first_twenty_judge_module": judge_imp,
        "loop_status_freshness_sec": {"ops": ops_age, "research": rs_age, "max_allowed_sec": stale_s},
        "systemd_probe": systemd,
        "deployed_smoke_commit_evidence": smoke_commit,
        "blockers": blockers,
        "ok": len(blockers) == 0,
        "honesty": "This gate proves non-live autonomy wiring + durable artifacts; it does not authorize live trading.",
    }

    out = runtime_root / "data" / "control" / "micro_trade_readiness.json"
    _write_json(out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

