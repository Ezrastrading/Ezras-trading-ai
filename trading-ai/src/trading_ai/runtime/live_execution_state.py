"""Operator-facing live execution state (JSON + human TXT)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

Mode = Literal["idle", "validating", "executing", "halted"]
Health = Literal["healthy", "warning", "halted"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _adapter(runtime_root: Optional[Path] = None) -> LocalStorageAdapter:
    return LocalStorageAdapter(runtime_root=runtime_root)


def read_live_execution_state(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    ad = _adapter(runtime_root)
    raw = ad.read_json("data/control/live_execution_state.json")
    if not raw:
        return default_live_state()
    base = default_live_state()
    base.update(raw)
    return base


def default_live_state() -> Dict[str, Any]:
    return {
        "updated_at": _iso(),
        "current_mode": "idle",
        "current_avenue": "",
        "current_gate": "",
        "last_action": "",
        "last_trade_id": "",
        "last_error": "",
        "last_success": True,
        "running_pnl_usd": 0.0,
        "trades_today": 0,
        "fail_count": 0,
        "system_health_status": "healthy",
    }


def write_live_execution_state(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    ad = _adapter(runtime_root)
    payload = dict(payload)
    payload["updated_at"] = _iso()
    ad.write_json("data/control/live_execution_state.json", payload)
    _write_txt_mirror(payload, runtime_root=runtime_root)


def _write_txt_mirror(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    root = Path(runtime_root or ezras_runtime_root())
    lines = [
        "EZRAS — LIVE EXECUTION STATE",
        "=" * 40,
        f"Updated (UTC): {payload.get('updated_at', '')}",
        f"Mode:         {payload.get('current_mode', '')}",
        f"Avenue:       {payload.get('current_avenue', '')}",
        f"Gate:         {payload.get('current_gate', '')}",
        f"Last action:  {payload.get('last_action', '')}",
        f"Last trade:   {payload.get('last_trade_id', '')}",
        f"Last success: {payload.get('last_success', '')}",
        f"Last error:   {payload.get('last_error', '')}",
        f"Running PnL:  {payload.get('running_pnl_usd', 0)} USD",
        f"Trades today: {payload.get('trades_today', 0)}",
        f"Fail count:   {payload.get('fail_count', 0)}",
        f"Health:       {payload.get('system_health_status', '')}",
        "=" * 40,
    ]
    p = root / "data" / "control" / "live_execution_state.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_execution_step(
    *,
    step: str,
    avenue: str = "",
    gate: str = "",
    mode: Optional[Mode] = None,
    trade_id: str = "",
    success: Optional[bool] = None,
    error: str = "",
    running_pnl_usd: Optional[float] = None,
    trades_today: Optional[int] = None,
    fail_count: Optional[int] = None,
    health: Optional[Health] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    cur = read_live_execution_state(runtime_root=runtime_root)
    cur["last_action"] = step
    if avenue:
        cur["current_avenue"] = avenue
    if gate:
        cur["current_gate"] = gate
    if mode:
        cur["current_mode"] = mode
    if trade_id:
        cur["last_trade_id"] = trade_id
    if success is not None:
        cur["last_success"] = success
        if not success and error:
            cur["last_error"] = error
        elif success:
            cur["last_error"] = ""
    elif error:
        cur["last_error"] = error
    if running_pnl_usd is not None:
        cur["running_pnl_usd"] = running_pnl_usd
    if trades_today is not None:
        cur["trades_today"] = trades_today
    if fail_count is not None:
        cur["fail_count"] = fail_count
    if health:
        cur["system_health_status"] = health
    write_live_execution_state(cur, runtime_root=runtime_root)
    return cur


def bump_fail_count(delta: int = 1, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    cur = read_live_execution_state(runtime_root=runtime_root)
    cur["fail_count"] = int(cur.get("fail_count") or 0) + delta
    if cur["fail_count"] > 0 and cur.get("system_health_status") == "healthy":
        cur["system_health_status"] = "warning"
    write_live_execution_state(cur, runtime_root=runtime_root)
    return cur


def reset_session_counters(*, runtime_root: Optional[Path] = None) -> None:
    """Operator hook: reset session-oriented counters in live state (not automatic)."""
    cur = read_live_execution_state(runtime_root=runtime_root)
    cur["fail_count"] = 0
    cur["system_health_status"] = "healthy"
    write_live_execution_state(cur, runtime_root=runtime_root)
