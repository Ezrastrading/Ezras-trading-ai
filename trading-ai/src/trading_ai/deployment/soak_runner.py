"""
Idle soak — scheduler / flush / reviews without live trading.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

from trading_ai.deployment.deployment_models import iso_now
from trading_ai.deployment.ops_outputs_proof import run_ops_outputs_bundle, verify_ops_outputs_proof
from trading_ai.deployment.paths import soak_report_path
from trading_ai.global_layer.review_scheduler import run_full_review_cycle, tick_scheduler
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.monitoring.supabase_reconciler import reconcile_once
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.shark.production_hardening.order_identity import is_duplicate_client_order_id

logger = logging.getLogger(__name__)


def run_proof_soak(hours: float = 2.0) -> Dict[str, Any]:
    """
    Non-trading system soak: scheduler ticks, optional queue flush, ops outputs.

    Writes ``data/deployment/soak_report.json``.
    """
    root = ezras_runtime_root()
    os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(root))
    os.environ.setdefault("GOVERNANCE_ORDER_ENFORCEMENT", "true")

    duration_sec = max(60.0, float(hours) * 3600.0)
    t_end = time.monotonic() + duration_sec
    st = ReviewStorage()
    st.ensure_review_files()

    tick_errors: List[str] = []
    ticks = 0
    dup_probe = is_duplicate_client_order_id("soak_idle_probe_nonexistent_id")

    while time.monotonic() < t_end:
        ticks += 1
        try:
            tick_scheduler(storage=st)
        except Exception as exc:
            tick_errors.append(f"{type(exc).__name__}:{exc}")
        try:
            run_full_review_cycle("midday", storage=st, skip_models=True)
        except Exception as exc:
            tick_errors.append(f"review_cycle:{type(exc).__name__}")
        try:
            reconcile_once()
        except Exception as exc:
            tick_errors.append(f"flush:{type(exc).__name__}")
        try:
            run_ops_outputs_bundle()
        except Exception as exc:
            tick_errors.append(f"ops:{type(exc).__name__}")
        time.sleep(30.0)

    ops = verify_ops_outputs_proof(write_file=True)
    rep: Dict[str, Any] = {
        "generated_at": iso_now(),
        "hours_requested": hours,
        "duration_sec": duration_sec,
        "scheduler_ticks_attempted": ticks,
        "tick_errors": tick_errors[:50],
        "duplicate_order_probe_false": not dup_probe,
        "ops_outputs_ok": ops.get("ops_outputs_ok"),
        "soak_ok": len(tick_errors) == 0 and bool(ops.get("ops_outputs_ok")),
    }
    soak_report_path().parent.mkdir(parents=True, exist_ok=True)
    soak_report_path().write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    return rep
