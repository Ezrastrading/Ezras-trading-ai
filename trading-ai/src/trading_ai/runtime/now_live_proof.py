"""Durable proof artifacts: autonomy stack vs live venue submission (must stay split)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.runtime.operating_system import assert_live_trading_env_disabled, enforce_non_live_env_defaults
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(runtime_root: Optional[Path]) -> Path:
    r = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(r)
    return r


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_now_live_autonomy_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    enforce_non_live_env_defaults()
    ok, why = assert_live_trading_env_disabled()
    root = _root(runtime_root)
    if not ok:
        payload = {
            "truth_version": "full_autonomy_now_live_mode_v1",
            "generated_at": _iso(),
            "ok": False,
            "blocked": True,
            "reason": why,
            "autonomy_stack": "blocked",
        }
        _write_json(root / "data" / "control" / "full_autonomy_now_live_mode.json", payload)
        _write_json(
            root / "data" / "control" / "full_autonomy_live_execution_status.json",
            {
                "truth_version": "full_autonomy_live_execution_status_v1",
                "generated_at": _iso(),
                "live_order_submission_enabled": True,
                "blocked": True,
                "reason": why,
            },
        )
        return payload

    mode = {
        "truth_version": "full_autonomy_now_live_mode_v1",
        "generated_at": _iso(),
        "ok": True,
        "blocked": False,
        "autonomy_stack": "now_live",
        "meaning": "Supervised autonomy loops are active-capable; venue live orders remain disabled by env contract.",
        "live_order_submission": False,
        "runtime_root": str(root),
        "env_snapshot": {
            "NTE_EXECUTION_MODE": (os.environ.get("NTE_EXECUTION_MODE") or "").strip(),
            "NTE_LIVE_TRADING_ENABLED": (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip(),
            "COINBASE_EXECUTION_ENABLED": (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip(),
        },
    }
    _write_json(root / "data" / "control" / "full_autonomy_now_live_mode.json", mode)
    live_stat = {
        "truth_version": "full_autonomy_live_execution_status_v1",
        "generated_at": _iso(),
        "live_order_submission_enabled": False,
        "blocked": False,
        "honesty": "Live venue submission is a separate operator-controlled contract; this artifact is false by design here.",
    }
    _write_json(root / "data" / "control" / "full_autonomy_live_execution_status.json", live_stat)
    return mode


def run_authoritative_live_guard_proof(*, runtime_root: Optional[Path] = None) -> Tuple[bool, Dict[str, Any]]:
    enforce_non_live_env_defaults()
    ok, why = assert_live_trading_env_disabled()
    root = _root(runtime_root)
    out = write_now_live_autonomy_artifacts(runtime_root=root)
    proof = {
        "truth_version": "authoritative_live_guard_proof_v1",
        "generated_at": _iso(),
        "live_env_ok": ok,
        "live_env_reason": why,
        "artifacts": {
            "now_live_mode": str(root / "data" / "control" / "full_autonomy_now_live_mode.json"),
            "live_execution_status": str(root / "data" / "control" / "full_autonomy_live_execution_status.json"),
        },
        "mode_payload_ok": bool(out.get("ok")),
    }
    _write_json(root / "data" / "control" / "authoritative_live_guard_proof.json", proof)
    return ok, proof
