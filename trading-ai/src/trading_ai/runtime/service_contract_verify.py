"""Deploy/service contract checks without importing ``trading_ai.deployment`` package init."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime.operating_system import assert_live_trading_env_disabled, enforce_non_live_env_defaults
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_autonomy_deploy_preflight(*, runtime_root: Optional[Path] = None) -> Tuple[bool, Dict[str, Any]]:
    enforce_non_live_env_defaults()
    ok_live, why = assert_live_trading_env_disabled()
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    reasons: List[str] = []
    if not ok_live:
        reasons.append(f"live_env:{why}")

    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    probe = ctrl / ".autonomy_preflight_write_probe"
    try:
        probe.write_text("ok\n", encoding="utf-8")
    except OSError as exc:
        reasons.append(f"runtime_root_not_writable:{exc.__class__.__name__}")

    require_refs = (os.environ.get("EZRAS_REQUIRE_DEPLOY_REFS") or "").strip().lower() in ("1", "true", "yes")
    primary = (os.environ.get("EZRAS_DEPLOY_PRIMARY_REF") or "").strip()
    secondary = (os.environ.get("EZRAS_DEPLOY_SECONDARY_REF") or "").strip()
    dual_ok = bool(primary and secondary)
    dual_report = {
        "require_env": require_refs,
        "primary_set": bool(primary),
        "secondary_set": bool(secondary),
        "honesty": "Set EZRAS_DEPLOY_PRIMARY_REF / EZRAS_DEPLOY_SECONDARY_REF to pin both repos for deploy auditing.",
    }
    if require_refs and not dual_ok:
        reasons.append("dual_repo_refs_required_but_missing")

    ok = not reasons
    payload: Dict[str, Any] = {
        "truth_version": "autonomy_deploy_preflight_v1",
        "generated_at": _iso(),
        "ok": ok,
        "runtime_root": str(root),
        "live_env_ok": ok_live,
        "live_env_reason": why,
        "dual_repo": dual_report,
        "fail_reasons": reasons,
    }
    _write_json(ctrl / "autonomy_deploy_preflight.json", payload)
    return ok, payload


def verify_systemd_unit_contract_templates(*, repo_root: Optional[Path] = None) -> Tuple[bool, Dict[str, Any]]:
    here = Path(__file__).resolve()
    proj = repo_root or here.parents[3]
    unit_dir = proj / "docs" / "systemd"
    findings: List[Dict[str, Any]] = []
    ok = True
    if not unit_dir.is_dir():
        ok = False
        findings.append({"path": str(unit_dir), "ok": False, "reason": "missing_unit_dir"})
    else:
        for p in sorted(unit_dir.glob("*.service")):
            txt = p.read_text(encoding="utf-8")
            oneshot = "Type=oneshot" in txt
            checks = {
                "has_exec_start": "ExecStart=" in txt,
                "has_restart": ("Restart=" in txt) or oneshot,
                "has_wanted_by": "WantedBy=" in txt,
                "mentions_trading_ai": "trading_ai.runtime" in txt or "trading-ai" in txt,
            }
            row_ok = all(checks.values())
            ok = ok and row_ok
            findings.append({"path": str(p), "ok": row_ok, "checks": checks})
    out = {
        "truth_version": "systemd_unit_contract_verify_v1",
        "generated_at": _iso(),
        "ok": ok,
        "unit_dir": str(unit_dir),
        "findings": findings,
    }
    root = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    _write_json(root / "data" / "control" / "systemd_unit_contract_verify.json", out)
    return ok, out
