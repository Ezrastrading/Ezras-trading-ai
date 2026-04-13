"""Build execution intents — Kelly × phase × tier × cluster × drawdown scaling."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.position_sizing_policy import HardCaps, clamp_to_hard_cap, default_caps_for_capital
from trading_ai.shark.capital_phase import (
    detect_phase,
    effective_kelly_base,
    phase_params,
    phase_tier_combined_multiplier,
)
from trading_ai.shark.kelly import apply_kelly_scaling, kelly_full_fraction
from trading_ai.shark.margin_control import check_margin_safety, effective_margin_pct_cap, get_margin_allowance
from trading_ai.shark.models import ExecutionIntent, HuntType, MarketSnapshot, OpportunityTier, ScoredOpportunity
from trading_ai.shark.state import LOSS_TRACKER

_log = logging.getLogger(__name__)

HUNT6_MAX_AGGREGATE_FRACTION = 0.08
HUNT6_KELLY_BASE = 0.25


def hunt6_aggregate_exposure_usd(positions_data: Optional[Dict[str, Any]]) -> float:
    """Sum notional for open positions tagged with Hunt 6."""
    if not positions_data:
        return 0.0
    s = 0.0
    for p in positions_data.get("open_positions") or []:
        for ht in p.get("hunt_types") or []:
            if ht == HuntType.NEAR_ZERO_ACCUMULATION.value:
                s += float(p.get("notional_usd", 0) or 0)
                break
    return s


def _has_near_zero(hunts: List) -> bool:
    return any(getattr(h, "hunt_type", None) == HuntType.NEAR_ZERO_ACCUMULATION for h in hunts)


def _pick_side(hunts: List) -> Tuple[str, float]:
    for h in hunts:
        if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION:
            return "yes", 0.0
    h0 = hunts[0]
    d = h0.details or {}
    if h0.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE:
        side = str(d.get("side", "yes"))
        return side, 0.0
    if h0.hunt_type == HuntType.STRUCTURAL_ARBITRAGE:
        return "both_leg_arb", 0.0
    return "yes", 0.0


def estimate_win_probability(m: MarketSnapshot, scored: ScoredOpportunity) -> float:
    if not scored.hunts:
        return max(m.yes_price, 0.01)
    for h in scored.hunts:
        if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION:
            return max(0.01, min(0.99, float((h.details or {}).get("base_rate", m.yes_price))))
    h = scored.hunts[0]
    if h.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE:
        if str((h.details or {}).get("side", "yes")) == "no":
            return max(0.01, min(0.99, 1.0 - float((h.details or {}).get("p_true", m.yes_price))))
        return float((h.details or {}).get("p_true", m.yes_price))
    return max(0.01, min(0.99, m.yes_price + scored.edge_size * 0.5))


def _price_for_kelly(m: MarketSnapshot, scored: ScoredOpportunity) -> float:
    h = scored.hunts[0]
    if _has_near_zero(scored.hunts):
        return m.yes_price
    if h.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE and str((h.details or {}).get("side")) == "no":
        return m.no_price
    return m.yes_price


def build_execution_intent(
    scored: ScoredOpportunity,
    *,
    capital: float,
    strategy_key: str = "shark_default",
    outlet: str,
    gap_exploitation_mode: bool = False,
    current_gap_exposure_fraction: float = 0.0,
    min_edge_effective: float | None = None,
    risk_position_multiplier: float = 1.0,
    market_category: str = "default",
    hunt6_aggregate_exposure_usd: float = 0.0,
    wallet_copy_trade: bool = False,
    is_mana: bool = False,
    current_drawdown_pct: float = 0.0,
) -> Optional[ExecutionIntent]:
    if scored.tier == OpportunityTier.BELOW_THRESHOLD:
        return None
    m = scored.market
    phase = detect_phase(capital)
    pp = phase_params(phase)
    min_e = min_edge_effective if min_edge_effective is not None else pp.min_edge
    if scored.edge_size < min_e:
        return None
    p_win = estimate_win_probability(m, scored)
    px = _price_for_kelly(m, scored)
    fk = kelly_full_fraction(p_win, px)
    if _has_near_zero(scored.hunts):
        k_base = HUNT6_KELLY_BASE
    else:
        k_base = effective_kelly_base(
            phase=phase,
            tier=scored.tier,
            gap_exploitation_mode=gap_exploitation_mode,
        )
    kelly_scaled = apply_kelly_scaling(fk, k_base)
    combined = phase_tier_combined_multiplier(phase, scored.tier)
    stake = kelly_scaled * combined
    loss_mult = LOSS_TRACKER.cluster_multiplier(
        strategy=strategy_key,
        hunt_type=scored.hunts[0].hunt_type,
        outlet=outlet,
        market_category=market_category,
    )
    stake *= loss_mult
    stake *= risk_position_multiplier
    if _has_near_zero(scored.hunts):
        room_h6 = HUNT6_MAX_AGGREGATE_FRACTION - (hunt6_aggregate_exposure_usd / max(capital, 1e-9))
        stake = min(stake, max(0.0, room_h6))
    caps = default_caps_for_capital(capital)
    stake = clamp_to_hard_cap(stake, HardCaps(caps.max_fraction_of_capital, caps.max_gap_total_fraction))
    stake = min(stake, pp.max_single_position_fraction)
    if gap_exploitation_mode:
        room = max(0.0, 0.60 - current_gap_exposure_fraction)
        stake = min(stake, room)
    side, _ = _pick_side(scored.hunts)
    if side == "both_leg_arb":
        side = "yes"
    exp_price = m.yes_price if side == "yes" else m.no_price
    notional = max(0.0, capital * stake)
    shr = max(1, int(notional / max(exp_price, 1e-6))) if exp_price > 0 else 1
    margin_borrowed = 0.0
    if not is_mana:
        margin_allowance = get_margin_allowance(
            capital=capital,
            confidence=scored.confidence,
            hunt_tier=scored.tier.value,
            current_drawdown_pct=current_drawdown_pct,
            near_zero_hunt=_has_near_zero(scored.hunts),
        )
        if notional > capital + 1e-9:
            if not check_margin_safety(notional, capital, margin_allowance):
                _log.info("Margin limit applied")
                notional = min(notional, capital)
                stake = notional / max(capital, 1e-9)
                shr = max(1, int(notional / max(exp_price, 1e-6))) if exp_price > 0 else 1
        margin_borrowed = max(0.0, notional - capital)
    u = m.underlying_data_if_available or {}
    meta = {
        "phase": phase.value,
        "tier": scored.tier.value,
        "token_id": u.get("token_id"),
        "condition_id": u.get("condition_id"),
        "market_category": market_category,
        "margin_borrowed": margin_borrowed,
        "margin_cap_pct": effective_margin_pct_cap(capital, scored.confidence) if not is_mana else 0.0,
    }
    if wallet_copy_trade:
        meta["trade_type"] = "wallet_copy"
    return ExecutionIntent(
        market_id=m.market_id,
        outlet=outlet,
        side=side,
        stake_fraction_of_capital=stake,
        edge_after_fees=scored.edge_size,
        estimated_win_probability=p_win,
        hunt_types=[h.hunt_type for h in scored.hunts],
        source="shark_gap" if gap_exploitation_mode else "shark_compounding",
        gap_exploit=gap_exploitation_mode,
        meta=meta,
        expected_price=exp_price,
        notional_usd=notional,
        shares=shr,
        is_mana=is_mana,
    )
