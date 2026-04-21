"""
Profit reality validation — deployment readiness from measured aggregates only.

Writes ``deployment_status.json`` under ``EZRAS_RUNTIME_ROOT/shark/state/``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.intelligence.edge_performance import (
    EdgePerformance,
    calculate_expectancy,
    is_consistent,
    is_profitable,
    latency_safe,
    max_drawdown,
)
from trading_ai.intelligence.equity_tracker import EquityTracker

logger = logging.getLogger(__name__)

DEPLOYMENT_STATUS_NAME = "deployment_status.json"
PROFIT_STATE_NAME = "profit_reality_state.json"


def deployment_status_path() -> Path:
    return shark_state_path(DEPLOYMENT_STATUS_NAME)


def profit_reality_state_path() -> Path:
    return shark_state_path(PROFIT_STATE_NAME)


def profit_reality_enforcement_enabled() -> bool:
    return (os.environ.get("EZRAS_PROFIT_REALITY_ENFORCEMENT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def is_deployment_ready(
    edge_perf: EdgePerformance,
    equity_tracker: EquityTracker,
    trade_pnls: List[float],
    latency_ms: float,
) -> Tuple[bool, str]:
    if edge_perf.trades < 20:
        return False, "not_enough_trades"
    if not is_profitable(edge_perf):
        return False, "not_profitable"
    if calculate_expectancy(edge_perf) <= 0:
        return False, "negative_expectancy"
    if not is_consistent(trade_pnls):
        return False, "inconsistent_performance"
    if not _drawdown_ok_equity(equity_tracker.curve):
        return False, "drawdown_too_high"
    if not equity_tracker.trend_up():
        return False, "equity_not_growing"
    if not latency_safe(latency_ms):
        return False, "latency_too_high"
    return True, "deployment_ready"


def _drawdown_ok_equity(curve: List[float]) -> bool:
    from trading_ai.intelligence.edge_performance import drawdown_ok

    try:
        lim = float((os.environ.get("PROFIT_REALITY_DD_LIMIT") or "0.1").strip() or "0.1")
    except ValueError:
        lim = 0.1
    return drawdown_ok(curve, limit=lim)


def load_profit_reality_state() -> Dict[str, Any]:
    p = profit_reality_state_path()
    if not p.is_file():
        return {"edge": {}, "equity_curve": [], "trade_pnls": [], "last_latency_ms": 0.0}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"edge": {}, "equity_curve": [], "trade_pnls": [], "last_latency_ms": 0.0}


def save_profit_reality_state(state: Dict[str, Any]) -> None:
    p = profit_reality_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def record_closed_trade_for_profit_reality(
    *,
    net_pnl: float,
    fees: float = 0.0,
    slippage: float = 0.0,
    equity_balance_after: Optional[float] = None,
    latency_ms: Optional[float] = None,
) -> None:
    """Append one closed trade into persisted aggregates (measurement hook)."""
    st = load_profit_reality_state()
    ep = EdgePerformance.from_dict(st.get("edge") if isinstance(st.get("edge"), dict) else {})
    ep.record_trade(net_pnl, fees, slippage)
    st["edge"] = ep.to_dict()
    pnls = list(st.get("trade_pnls") or [])
    pnls.append(float(net_pnl))
    st["trade_pnls"] = pnls[-500:]
    if equity_balance_after is not None:
        curve = list(st.get("equity_curve") or [])
        curve.append(float(equity_balance_after))
        st["equity_curve"] = curve[-2000:]
    if latency_ms is not None:
        st["last_latency_ms"] = float(latency_ms)
    save_profit_reality_state(st)
    evaluate_and_write_deployment_status()


def evaluate_and_write_deployment_status() -> Tuple[bool, str, Dict[str, Any]]:
    st = load_profit_reality_state()
    ep = EdgePerformance.from_dict(st.get("edge") if isinstance(st.get("edge"), dict) else {})
    eq = EquityTracker.from_dict({"curve": st.get("equity_curve") or []})
    trade_pnls = [float(x) for x in (st.get("trade_pnls") or [])]
    lat = float(st.get("last_latency_ms") or 0.0)
    ready, reason = is_deployment_ready(ep, eq, trade_pnls, lat)
    dd = max_drawdown(eq.curve) if eq.curve else 0.0
    peak = max(eq.curve) if eq.curve else 0.0
    dd_frac = (dd / peak) if peak > 0 else 0.0
    exp = calculate_expectancy(ep) if ep.trades else 0.0
    rnp = float(ep.net_pnl - ep.total_fees - ep.total_slippage)
    payload: Dict[str, Any] = {
        "ready": ready,
        "reason": reason,
        "trades": ep.trades,
        "net_profit": rnp,
        "expectancy": exp,
        "drawdown": dd_frac,
        "gross_net_pnl": ep.net_pnl,
        "total_fees": ep.total_fees,
        "total_slippage": ep.total_slippage,
    }
    _write_deployment_status_json(payload)
    try:
        from trading_ai.organism.deployment_metrics import load_deployment_metrics, save_deployment_metrics

        m = load_deployment_metrics()
        m["DEPLOYMENT_READY"] = bool(ready)
        m["profit_reality_reason"] = reason
        save_deployment_metrics(m)
    except Exception:
        logger.debug("deployment_metrics merge skipped", exc_info=True)
    return ready, reason, payload


def _write_deployment_status_json(payload: Dict[str, Any]) -> None:
    p = deployment_status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def scaling_permitted() -> Tuple[bool, str]:
    """Capital scaling / larger notionals only when profit reality is proven (if enforcement on)."""
    if not profit_reality_enforcement_enabled():
        return True, "enforcement_off"
    ready, reason, _ = evaluate_and_write_deployment_status()
    return ready, reason


def promotion_permitted() -> Tuple[bool, str]:
    """New edge / strategy promotion only when deployment is ready (if enforcement on)."""
    return scaling_permitted()


def assert_scaling_permitted() -> None:
    if not profit_reality_enforcement_enabled():
        return
    ok, reason = scaling_permitted()
    if not ok:
        raise RuntimeError(f"PROFIT_REALITY: scaling blocked — {reason}")


def assert_promotion_permitted() -> None:
    if not profit_reality_enforcement_enabled():
        return
    ok, reason = promotion_permitted()
    if not ok:
        raise RuntimeError(f"PROFIT_REALITY: promotion blocked — {reason}")


def full_scale_notional_permitted() -> bool:
    ok, _ = scaling_permitted()
    return ok
