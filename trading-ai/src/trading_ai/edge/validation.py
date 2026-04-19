"""Strict validation and promotion rules for edges."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Tuple

from trading_ai.edge.models import EdgeStatus, EdgeTradeMetrics
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.scoring import compute_edge_metrics, trades_for_edge


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def minimum_sample_trades() -> int:
    return _env_int("EDGE_MIN_SAMPLE_TRADES", 35)


def max_drawdown_usd_threshold() -> float:
    """Reject promotion if max drawdown exceeds this (absolute USD)."""
    return _env_float("EDGE_MAX_DRAWDOWN_USD", 10_000.0)


def slippage_spike_bps() -> float:
    return _env_float("EDGE_SLIPPAGE_SPIKE_BPS", 250.0)


def evaluate_edge(
    registry: EdgeRegistry,
    events: List[Mapping[str, Any]],
    edge_id: str,
) -> Dict[str, Any]:
    """
    Score edge and return promotion recommendation without mutating registry
    (caller applies updates).
    """
    m = compute_edge_metrics(events, edge_id)
    rows = trades_for_edge(events, edge_id)
    structural = _structural_issues(rows)
    min_n = minimum_sample_trades()
    dd_cap = max_drawdown_usd_threshold()

    reasons: List[str] = []
    promote_to: Optional[str] = None

    if m.total_trades >= min_n and m.net_pnl > 0 and m.post_fee_expectancy > 0:
        if m.max_drawdown <= dd_cap and not structural["hard_fail"]:
            promote_to = EdgeStatus.VALIDATED.value
            reasons.append("sample_ok_net_positive_post_fee_expectancy_ok_drawdown_ok")
        else:
            reasons.append(f"hold:drawdown_or_structure dd={m.max_drawdown:.4f} cap={dd_cap}")
    else:
        if m.total_trades < min_n:
            reasons.append(f"insufficient_sample:{m.total_trades}<{min_n}")
        if m.net_pnl <= 0:
            reasons.append("net_pnl_non_positive")
        if m.post_fee_expectancy <= 0:
            reasons.append("post_fee_expectancy_non_positive")

    reject = False
    if m.post_fee_expectancy <= 0 and m.total_trades >= min_n:
        reject = True
        reasons.append("reject:expectancy_after_fees_non_positive")
    if _fee_erosion(rows):
        reject = True
        reasons.append("reject:repeated_fee_erosion")
    if structural["hard_fail"]:
        reject = True
        reasons.append("reject:structural_execution")

    if reject:
        promote_to = EdgeStatus.REJECTED.value

    return {
        "edge_id": edge_id,
        "metrics": m,
        "metrics_dict": {
            "total_trades": m.total_trades,
            "win_rate": m.win_rate,
            "avg_win": m.avg_win,
            "avg_loss": m.avg_loss,
            "expectancy": m.expectancy,
            "post_fee_expectancy": m.post_fee_expectancy,
            "net_pnl": m.net_pnl,
            "pnl_per_trade": m.pnl_per_trade,
            "drawdown": m.max_drawdown,
            "variance": m.variance_pnl,
            "stability_score": m.stability_score,
        },
        "promote_to": promote_to,
        "reject": reject,
        "reasons": reasons,
        "structural": structural,
    }


def _fee_erosion(rows: List[Mapping[str, Any]]) -> bool:
    """Heuristic: many trades where fees dominate negative outcome."""
    bad = 0
    for r in rows:
        try:
            net = float(r.get("net_pnl") or 0)
            fees = float(r.get("fees_paid") or 0)
        except (TypeError, ValueError):
            continue
        if net < 0 and fees > abs(net) * 0.85:
            bad += 1
    return bad >= max(5, len(rows) // 4) and len(rows) >= 10


def _structural_issues(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    spike = slippage_spike_bps()
    hard_fail = False
    spikes = 0
    for r in rows:
        for k in ("entry_slippage_bps", "exit_slippage_bps"):
            try:
                v = float(r.get(k) or 0)
            except (TypeError, ValueError):
                continue
            if v > spike:
                spikes += 1
        if str(r.get("health_state") or "") == "error":
            hard_fail = True
        flags = r.get("anomaly_flags") or []
        if isinstance(flags, list) and "data_pipeline_failure" in [str(x) for x in flags]:
            hard_fail = True
    if spikes >= max(3, len(rows) // 5) and len(rows) >= 8:
        hard_fail = True
    return {"hard_fail": hard_fail, "slippage_spike_count": spikes}


def apply_evaluation(
    registry: EdgeRegistry,
    events: List[Mapping[str, Any]],
    edge_id: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Update registry status from evaluation. Returns (changed, report)."""
    edge = registry.get(edge_id)
    if edge is None:
        return False, {"error": "unknown_edge", "edge_id": edge_id}
    if edge.status in (EdgeStatus.REJECTED.value, EdgeStatus.DEPRECATED.value):
        return False, {"skipped": True, "reason": "terminal_status", "status": edge.status}

    rep = evaluate_edge(registry, events, edge_id)
    target = rep.get("promote_to")
    if not target:
        return False, rep

    if target == EdgeStatus.VALIDATED.value and edge.status in (
        EdgeStatus.CANDIDATE.value,
        EdgeStatus.TESTING.value,
    ):
        registry.update_status(edge_id, EdgeStatus.VALIDATED.value, reason=";".join(rep["reasons"][:3]))
        return True, rep
    if target == EdgeStatus.REJECTED.value:
        registry.update_status(
            edge_id,
            EdgeStatus.REJECTED.value,
            reason=";".join(rep["reasons"][:5]),
        )
        return True, rep
    return False, rep


def promote_testing_if_candidate(registry: EdgeRegistry, edge_id: str) -> bool:
    """candidate → testing (manual or batch)."""
    e = registry.get(edge_id)
    if e is None or e.status != EdgeStatus.CANDIDATE.value:
        return False
    registry.update_status(edge_id, EdgeStatus.TESTING.value, reason="entered_controlled_testing")
    return True


def promote_validated_to_scaled(registry: EdgeRegistry, edge_id: str) -> bool:
    """validated → scaled (capital ramp eligible)."""
    e = registry.get(edge_id)
    if e is None or e.status != EdgeStatus.VALIDATED.value:
        return False
    registry.update_status(edge_id, EdgeStatus.SCALED.value, reason="capital_allocation_ramp")
    return True
