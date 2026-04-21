"""Compact ratio context for trade events — embed via market_snapshot_json fold."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from trading_ai.ratios.universal_ratio_registry import RatioPolicyBundle, build_universal_ratio_policy_bundle


def build_ratio_context_for_trade_event(
    *,
    trading_gate: str,
    avenue_id: str,
    strategy_id: str,
    edge_id: Optional[str] = None,
    bundle: Optional[RatioPolicyBundle] = None,
    deployable_capital_at_decision: Optional[float] = None,
    reserve_capital_at_decision: Optional[float] = None,
    confidence_multiplier_active: float = 1.0,
    operating_mode: str = "normal",
) -> Dict[str, Any]:
    b = bundle or build_universal_ratio_policy_bundle(operating_mode=operating_mode)
    uni = b.universal_ratios
    gf = float((uni.get("universal.per_trade_cap_fraction") or {}).get("value") or 1.0)
    af = 1.0
    if trading_gate and "gate_b" in str(trading_gate).lower():
        gbo = b.gate_overlays.get("gate_b") or {}
        m = gbo.get("gate.gate_b.momentum_safe_deployable_fraction") or {}
        gf = float(m.get("value") or gf)
    ctx = {
        "ratio_policy_version": b.ratio_policy_version,
        "active_operating_mode": operating_mode,
        "deployable_capital_at_decision": deployable_capital_at_decision,
        "reserve_capital_at_decision": reserve_capital_at_decision,
        "per_trade_cap_fraction": (uni.get("universal.per_trade_cap_fraction") or {}).get("value"),
        "max_daily_drawdown_ratio_active": (uni.get("universal.max_daily_drawdown_ratio") or {}).get("value"),
        "profit_target_ratio_active": (uni.get("universal.profit_target_min_ratio") or {}).get("value"),
        "max_loss_ratio_active": (uni.get("universal.stop_loss_max_ratio") or {}).get("value"),
        "trailing_ratio_active": (b.gate_overlays.get("gate_a") or {}).get(
            "gate.gate_a.trailing_ratio_ref", {}
        ).get("value"),
        "gate_fraction_active": gf,
        "avenue_fraction_active": af,
        "edge_fraction_active": 1.0,
        "strategy_fraction_active": 1.0,
        "concentration_ratio_active": (uni.get("universal.max_concurrent_exposure_ratio") or {}).get("value"),
        "confidence_multiplier_active": confidence_multiplier_active,
        "route_cost_ratio": None,
        "liquidity_size_ratio": None,
        "execution_quality_floor_active": None,
        "ratio_scope_sources": ["universal_ratio_registry", "nte_settings"],
        "ratio_override_flags": {"operator": False, "adaptive": False},
        "trading_gate": trading_gate,
        "avenue_id": avenue_id,
        "strategy_id": strategy_id,
        "edge_id": edge_id,
        "labeling": {
            "scope_type_trade": "edge" if edge_id else "strategy",
            "inherited_from": "universal_ratio_policy_v1",
        },
    }
    return ctx


def enrich_closed_trade_raw_with_ratio_context_if_absent(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Non-destructive: if ``ratio_context`` already set, return as dict unchanged.

    Otherwise attach compact context from registry (advisory — does not change execution).
    """
    out = dict(raw)
    if out.get("ratio_context") is not None:
        return out
    tg = str(out.get("trading_gate") or out.get("trading_gate_id") or "")
    return {
        **out,
        "ratio_context": build_ratio_context_for_trade_event(
            trading_gate=tg or "unspecified",
            avenue_id=str(out.get("avenue_id") or ""),
            strategy_id=str(out.get("strategy_id") or ""),
            edge_id=out.get("edge_id"),
        ),
    }
