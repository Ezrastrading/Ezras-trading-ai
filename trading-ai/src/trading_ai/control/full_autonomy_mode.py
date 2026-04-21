"""
Authoritative runtime mode for full autonomy while forbidding live trading.

This module is intentionally small and dependency-light so it can be imported early by
server entrypoints (services, daemon runners, sim harness).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

FullAutonomyMode = Literal["FULL_AUTONOMY_NONLIVE", "DISABLED"]


@dataclass(frozen=True)
class FullAutonomyModeState:
    mode: FullAutonomyMode
    live_trading_disabled: bool
    autonomy_enabled: bool
    daemons_enabled: bool
    orchestration_enabled: bool
    research_enabled: bool
    learning_enabled: bool
    review_enabled: bool
    task_routing_enabled: bool
    simulation_enabled: bool
    source_of_truth: str
    ts_unix: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _truth_path(runtime_root: Path) -> str:
    return "data/control/full_autonomy_mode.json"


def _status_path(runtime_root: Path) -> str:
    return "data/control/full_autonomy_live_status.json"


def _env_mode() -> Optional[FullAutonomyMode]:
    raw = (os.environ.get("EZRAS_FULL_AUTONOMY_MODE") or "").strip().upper()
    if raw in ("FULL_AUTONOMY_NONLIVE",):
        return "FULL_AUTONOMY_NONLIVE"
    if raw in ("DISABLED", "OFF", "0", "FALSE", "NO"):
        return "DISABLED"
    return None


def read_full_autonomy_mode(*, runtime_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    root = (runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    raw = ad.read_json(_truth_path(root))
    return raw if isinstance(raw, dict) else None


def resolve_full_autonomy_mode(*, runtime_root: Optional[Path] = None) -> FullAutonomyModeState:
    """
    Resolve the authoritative mode.

    Precedence:
    - explicit env `EZRAS_FULL_AUTONOMY_MODE`
    - persisted `data/control/full_autonomy_mode.json`
    - default DISABLED
    """
    root = (runtime_root or ezras_runtime_root()).resolve()
    env = _env_mode()
    if env is not None:
        mode: FullAutonomyMode = env
        src = "env:EZRAS_FULL_AUTONOMY_MODE"
    else:
        stored = read_full_autonomy_mode(runtime_root=root) or {}
        raw = str(stored.get("mode") or "").strip().upper()
        mode = "FULL_AUTONOMY_NONLIVE" if raw == "FULL_AUTONOMY_NONLIVE" else "DISABLED"
        src = "artifact:data/control/full_autonomy_mode.json" if stored else "default"

    if mode == "FULL_AUTONOMY_NONLIVE":
        return FullAutonomyModeState(
            mode=mode,
            live_trading_disabled=True,
            autonomy_enabled=True,
            daemons_enabled=True,
            orchestration_enabled=True,
            research_enabled=True,
            learning_enabled=True,
            review_enabled=True,
            task_routing_enabled=True,
            simulation_enabled=True,
            source_of_truth=src,
            ts_unix=time.time(),
        )
    return FullAutonomyModeState(
        mode="DISABLED",
        live_trading_disabled=True,
        autonomy_enabled=False,
        daemons_enabled=False,
        orchestration_enabled=False,
        research_enabled=False,
        learning_enabled=False,
        review_enabled=False,
        task_routing_enabled=False,
        simulation_enabled=False,
        source_of_truth=src,
        ts_unix=time.time(),
    )


def apply_full_autonomy_nonlive_env() -> Dict[str, str]:
    """
    Apply fail-closed env flags for "autonomy live, trading disabled".

    Returns a dict of key->value applied (for artifact transparency).
    """
    updates: Dict[str, str] = {}

    # Explicit mode + scope
    updates["EZRAS_FULL_AUTONOMY_MODE"] = "FULL_AUTONOMY_NONLIVE"
    updates["NTE_EXECUTION_MODE"] = "paper"
    updates["NTE_EXECUTION_SCOPE"] = "paper"
    updates["NTE_PAPER_MODE"] = "true"
    updates["NTE_DRY_RUN"] = "true"
    updates["EZRAS_DRY_RUN"] = "true"

    # Hard disable live trading flags (multiple independent gates exist; keep them all off).
    updates["NTE_LIVE_TRADING_ENABLED"] = "false"
    updates["COINBASE_ENABLED"] = "false"
    updates["COINBASE_EXECUTION_ENABLED"] = "false"
    updates["GATE_B_LIVE_EXECUTION_ENABLED"] = "false"

    # Guard expects live route to be "live" for orders; set to a non-live value to fail closed.
    updates["NTE_COINBASE_EXECUTION_ROUTE"] = "paper"

    # Runner/daemon layers: run tick/paper only unless an operator later flips modes.
    updates.setdefault("EZRAS_RUNNER_MODE", "paper_execution")
    updates.setdefault("EZRAS_AVENUE_A_DAEMON_MODE", "paper_execution")

    for k, v in updates.items():
        os.environ[k] = v
    return updates


def write_full_autonomy_mode_artifacts(
    *,
    runtime_root: Optional[Path] = None,
    reason: str = "enable_full_autonomy_nonlive",
) -> Dict[str, Any]:
    root = (runtime_root or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    ad = LocalStorageAdapter(runtime_root=root)

    env_applied = apply_full_autonomy_nonlive_env()
    st = resolve_full_autonomy_mode(runtime_root=root)

    mode_payload: Dict[str, Any] = {
        **st.to_dict(),
        "runtime_root": str(root),
        "reason": reason,
        "env_applied": env_applied,
        "honesty": (
            "FULL_AUTONOMY_NONLIVE means autonomy loops/daemons may run, but any live venue order must be blocked. "
            "Live enablement requires separate operator actions and passing universal live guard + live_order_guard."
        ),
    }
    ad.write_json(_truth_path(root), mode_payload)
    ad.write_text("data/control/full_autonomy_mode.txt", json.dumps(mode_payload, indent=2, default=str) + "\n")

    # Status artifact: keep this separate so operators can tail one small file.
    status = {
        "ts_unix": time.time(),
        "runtime_root": str(root),
        "mode": st.mode,
        "operational_autonomy_live": bool(st.autonomy_enabled and st.daemons_enabled and st.orchestration_enabled),
        "live_trading_disabled": True,
        "live_orders_allowed": False,
        "source_of_truth": st.source_of_truth,
        "reason": reason,
        "enforcement": {
            "nte_execution_mode": os.environ.get("NTE_EXECUTION_MODE"),
            "nte_live_trading_enabled": os.environ.get("NTE_LIVE_TRADING_ENABLED"),
            "coinbase_enabled": os.environ.get("COINBASE_ENABLED"),
            "coinbase_execution_enabled": os.environ.get("COINBASE_EXECUTION_ENABLED"),
            "nte_coinbase_execution_route": os.environ.get("NTE_COINBASE_EXECUTION_ROUTE"),
        },
        "honesty": "live_orders_allowed=false is asserted by mode; proof requires live-guard smoke + order-guard logs.",
    }
    ad.write_json(_status_path(root), status)
    return {"mode": mode_payload, "status": status}

