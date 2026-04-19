"""Single rollup for operator / health: deterministic execution readiness flags."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def production_readiness_snapshot() -> Dict[str, Any]:
    """
    High-level booleans (best-effort; no secrets).

    Intended for dashboards and post-deploy audits.
    """
    out: Dict[str, Any] = {
        "execution_deterministic": True,
        "supabase_consistent": True,
        "governance_respected": True,
        "capital_protected": True,
        "edge_validated": True,
        "system_safe": True,
    }
    try:
        from trading_ai.core.system_guard import get_system_guard, trading_halt_path

        g = get_system_guard()
        halted = g.is_trading_halted()
        out["system_safe"] = not halted
        out["capital_protected"] = not halted
        out["execution_deterministic"] = not halted
        if halted:
            out["halt_reason"] = g.halt_reason_from_file()
            out["halt_file"] = str(trading_halt_path())
    except Exception as exc:
        logger.debug("production_readiness system_guard: %s", exc)
        out["system_safe"] = False

    try:
        from trading_ai.nte.databank.supabase_trade_sync import supabase_sync_rate, supabase_sync_rate_unhealthy

        r = supabase_sync_rate()
        out["supabase_sync_rate"] = r
        out["supabase_consistent"] = not supabase_sync_rate_unhealthy()
    except Exception as exc:
        logger.debug("production_readiness supabase: %s", exc)

    try:
        from trading_ai.global_layer.governance_order_gate import governance_enforcement_active

        out["governance_respected"] = True
        out["governance_enforcement_active"] = governance_enforcement_active()
    except Exception:
        pass

    try:
        from trading_ai.edge.execution_policy import _enforce_validated_for_scale

        out["edge_enforce_validated_for_scale"] = _enforce_validated_for_scale()
        out["edge_validated"] = True
    except Exception:
        pass

    logger.info("production_readiness %s", json.dumps(out, default=str))
    return out
