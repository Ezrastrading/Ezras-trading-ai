"""Persistent counters for deployment readiness (failure rate, totals)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from trading_ai.organism.paths import organism_dir

DEFAULT_METRICS: Dict[str, Any] = {
    "total_trades": 0,
    "failed_trades": 0,
    "supabase_failures": 0,
    "execution_errors": 0,
    "READY_FOR_FIRST_20": False,
    "no_partial_failures": True,
    "pnl_verified": False,
    "sell_success": False,
    "execution_success": False,
    "supabase_synced": False,
    "DEPLOYMENT_READY": False,
}


def deployment_metrics_path() -> Path:
    return organism_dir() / "deployment_metrics.json"


def load_deployment_metrics() -> Dict[str, Any]:
    p = deployment_metrics_path()
    if not p.is_file():
        return dict(DEFAULT_METRICS)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULT_METRICS)
        out = dict(DEFAULT_METRICS)
        out.update(raw)
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(DEFAULT_METRICS)


def save_deployment_metrics(d: Dict[str, Any]) -> None:
    p = deployment_metrics_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")


def failure_rate(metrics: Dict[str, Any]) -> float:
    total = int(metrics.get("total_trades") or 0)
    failed = int(metrics.get("failed_trades") or 0)
    if total <= 0:
        return 0.0
    return float(failed) / float(total)


def record_trade_outcome(
    *,
    success: bool,
    supabase_ok: bool,
    execution_error: bool = False,
) -> Dict[str, Any]:
    m = load_deployment_metrics()
    m["total_trades"] = int(m.get("total_trades") or 0) + 1
    if not success:
        m["failed_trades"] = int(m.get("failed_trades") or 0) + 1
    if not supabase_ok:
        m["supabase_failures"] = int(m.get("supabase_failures") or 0) + 1
    if execution_error:
        m["execution_errors"] = int(m.get("execution_errors") or 0) + 1
    fr = failure_rate(m)
    max_fr = float((os.environ.get("EZRAS_DEPLOYMENT_MAX_FAILURE_RATE") or "0.02").strip() or "0.02")
    if m["total_trades"] >= 3 and fr > max_fr:
        from trading_ai.core.system_guard import get_system_guard

        get_system_guard().halt_now(f"TOO MANY FAILURES: failure_rate={fr:.4f}>{max_fr}")
    save_deployment_metrics(m)
    return m


def compute_deployment_ready(
    *,
    ready_for_first_20: bool,
    total_trades: int,
    failure_rate_value: float,
) -> bool:
    max_fr = float((os.environ.get("EZRAS_DEPLOYMENT_MAX_FAILURE_RATE") or "0.02").strip() or "0.02")
    return bool(ready_for_first_20 and total_trades >= 20 and failure_rate_value < max_fr)


def assert_scaling_allowed_if_enforced() -> None:
    if not deployment_scaling_enforced():
        return
    m = load_deployment_metrics()
    if not bool(m.get("DEPLOYMENT_READY")):
        raise RuntimeError("SCALING BLOCKED — deployment not ready (see deployment_metrics.json)")


def merge_validation_proof(metrics_update: Dict[str, Any]) -> Dict[str, Any]:
    m = load_deployment_metrics()
    m.update(metrics_update)
    save_deployment_metrics(m)
    return m
