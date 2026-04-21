"""
Explicit operator enable for autonomous daemon **live order submission**.

ARMED_BUT_OFF: daemon may loop, refresh truth, tick, research — but live venue orders require
both ``data/control/autonomous_daemon_live_enable.json`` (confirmed) and
``EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.orchestration.daemon_live_authority import compute_env_fingerprint
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

REL_ENABLE = "data/control/autonomous_daemon_live_enable.json"
REL_EXAMPLE = "data/control/autonomous_daemon_live_enable.example.json"
ENV_LIVE_ENABLED = "EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_autonomous_daemon_live_enable(*, runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    return ad.read_json(REL_ENABLE) or {}


def env_autonomous_daemon_live_enabled() -> bool:
    return (os.environ.get(ENV_LIVE_ENABLED) or "").strip().lower() in ("1", "true", "yes")


def autonomous_daemon_live_enable_reasons(*, runtime_root: Path) -> Tuple[bool, List[str]]:
    """Both gates must pass for live order submission."""
    blockers: List[str] = []
    raw = read_autonomous_daemon_live_enable(runtime_root=runtime_root)
    if not raw.get("schema_version"):
        blockers.append("autonomous_daemon_live_enable_artifact_missing_or_invalid_schema")
    if raw.get("confirmed") is not True:
        blockers.append("autonomous_daemon_live_enable_confirmed_not_true")
    if not env_autonomous_daemon_live_enabled():
        blockers.append(f"{ENV_LIVE_ENABLED}_not_true")
    return (len(blockers) == 0), blockers


def autonomous_daemon_may_submit_live_orders(*, runtime_root: Path) -> Tuple[bool, List[str]]:
    """Venue live orders (Gate A validation path) — requires dual gate."""
    return autonomous_daemon_live_enable_reasons(runtime_root=runtime_root)


def write_autonomous_daemon_live_enable_example(*, runtime_root: Path) -> Dict[str, Any]:
    """Operator copies to autonomous_daemon_live_enable.json and sets confirmed + gates."""
    root = Path(runtime_root).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    ex = {
        "schema_version": 1,
        "confirmed": False,
        "avenue_ids_enabled": ["A"],
        "gate_ids_enabled": ["gate_a"],
        "mode": "autonomous_daemon_live",
        "operator": "",
        "created_at_utc": None,
        "updated_at_utc": None,
        "note": (
            "Set confirmed true only after supervised proof and policy review. "
            "Also export EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true in the daemon process environment."
        ),
        "runtime_root": str(root),
        "fingerprint_if_any": "",
    }
    p = ctrl / "autonomous_daemon_live_enable.example.json"
    p.write_text(json.dumps(ex, indent=2) + "\n", encoding="utf-8")
    return {"path": str(p), "example": ex}


def write_autonomous_daemon_live_enable_guidance(*, runtime_root: Path) -> Dict[str, Any]:
    """Non-enabling guidance artifact — does not set confirmed."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    fp = compute_env_fingerprint()
    payload = {
        "truth_version": "autonomous_daemon_live_enable_guidance_v1",
        "generated_at": _iso(),
        "runtime_root": str(Path(runtime_root).resolve()),
        "env_fingerprint_at_write": fp,
        "required_for_live_orders": {
            "artifact": REL_ENABLE,
            "artifact_field": "confirmed must be true",
            "env": f"{ENV_LIVE_ENABLED}=true",
            "honesty": "Missing either gate => ARMED_BUT_OFF only — no venue orders.",
        },
        "example_path": REL_EXAMPLE,
    }
    ad.write_json("data/control/autonomous_daemon_live_enable_guidance.json", payload)
    ad.write_text("data/control/autonomous_daemon_live_enable_guidance.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def arm_autonomous_daemon_live_enable_file(
    *,
    runtime_root: Path,
    confirmed: bool,
    avenue_ids: List[str],
    gate_ids: List[str],
    operator: str,
    note: str = "",
) -> Dict[str, Any]:
    """
    ``daemon-arm-live`` — writes the enable file structure; default confirmed=False unless operator sets True.
    Does not export env; does not place orders.
    """
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    now = _iso()
    prev = ad.read_json(REL_ENABLE) or {}
    created = str(prev.get("created_at_utc") or now)
    payload = {
        "schema_version": 1,
        "confirmed": bool(confirmed),
        "avenue_ids_enabled": list(avenue_ids),
        "gate_ids_enabled": list(gate_ids),
        "mode": "autonomous_daemon_live",
        "operator": str(operator).strip(),
        "created_at_utc": created,
        "updated_at_utc": now,
        "note": str(note).strip(),
        "runtime_root": str(root),
        "fingerprint_if_any": compute_env_fingerprint(),
    }
    ad.write_json(REL_ENABLE, payload)
    ad.write_text(REL_ENABLE.replace(".json", ".txt"), json.dumps(payload, indent=2) + "\n")
    return payload


def disarm_autonomous_daemon_live_enable_file(*, runtime_root: Path) -> Dict[str, Any]:
    """Sets confirmed false — live orders blocked even if env is set."""
    raw = read_autonomous_daemon_live_enable(runtime_root=runtime_root)
    if not raw:
        raw = {"schema_version": 1}
    raw["confirmed"] = False
    raw["updated_at_utc"] = _iso()
    raw["note"] = (str(raw.get("note") or "") + " disarmed_by_operator_cli").strip()
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_json(REL_ENABLE, raw)
    ad.write_text(REL_ENABLE.replace(".json", ".txt"), json.dumps(raw, indent=2) + "\n")
    return raw
