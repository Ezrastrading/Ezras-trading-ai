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
from trading_ai.shark import ceo_sessions
from trading_ai.shark.kelly import apply_kelly_scaling, kelly_full_fraction
from trading_ai.shark.margin_control import check_margin_safety, effective_margin_pct_cap, get_margin_allowance
from trading_ai.shark.models import (
    CapitalPhase,
    ExecutionIntent,
    HuntSignal,
    HuntType,
    MarketSnapshot,
    OpportunityTier,
    ScoredOpportunity,
)
from trading_ai.shark.state import LOSS_TRACKER

_log = logging.getLogger(__name__)

MIN_POSITION_USD = 1.0
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


def _execution_primary_hunt(hunts: List) -> HuntSignal:
    """Prefer Hunt 6 if present; else the strongest edge (multiple hunts on one market)."""
    for h in hunts:
        if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION:
            return h
    return max(hunts, key=lambda h: float(h.edge_after_fees))


def _pick_side(hunts: List) -> Tuple[str, float]:
    for h in hunts:
        if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION:
            return "yes", 0.0
    h0 = _execution_primary_hunt(hunts)
    d = h0.details or {}
    if h0.hunt_type == HuntType.OPTIONS_BINARY:
        return str(d.get("side", "yes")), 0.0
    if h0.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE:
        side = str(d.get("side", "yes"))
        return side, 0.0
    if h0.hunt_type == HuntType.STRUCTURAL_ARBITRAGE:
        return "both_leg_arb", 0.0
    if h0.hunt_type in (
        HuntType.CRYPTO_SCALP,
        HuntType.NEAR_RESOLUTION,
        HuntType.ORDER_BOOK_IMBALANCE,
        HuntType.VOLUME_SPIKE,
        HuntType.KALSHI_NEAR_CLOSE,
        HuntType.KALSHI_CONVERGENCE,
        HuntType.KALSHI_MOMENTUM,
    ):
        return str(d.get("side", "yes")), 0.0
    return "yes", 0.0


def estimate_win_probability(m: MarketSnapshot, scored: ScoredOpportunity) -> float:
    if not scored.hunts:
        return max(m.yes_price, 0.01)
    for h in scored.hunts:
        if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION:
            return max(0.01, min(0.99, float((h.details or {}).get("base_rate", m.yes_price))))
    h = _execution_primary_hunt(scored.hunts)
    d = h.details or {}
    if h.hunt_type == HuntType.CRYPTO_SCALP:
        return max(0.01, min(0.99, float(d.get("true_prob", m.yes_price))))
    if h.hunt_type == HuntType.NEAR_RESOLUTION:
        side = str(d.get("side", "yes"))
        px = m.yes_price if side == "yes" else m.no_price
        return max(0.93, min(0.995, px + 0.01))
    if h.hunt_type == HuntType.ORDER_BOOK_IMBALANCE:
        side = str(d.get("side", "yes"))
        return m.yes_price if side == "yes" else m.no_price
    if h.hunt_type == HuntType.VOLUME_SPIKE:
        side = str(d.get("side", "yes"))
        return m.yes_price if side == "yes" else m.no_price
    if h.hunt_type == HuntType.PURE_ARBITRAGE:
        return 0.99
    if h.hunt_type in (HuntType.KALSHI_NEAR_CLOSE, HuntType.KALSHI_CONVERGENCE):
        side = str((h.details or {}).get("side", "yes"))
        px = m.yes_price if side == "yes" else m.no_price
        return max(0.01, min(0.99, float(px)))
    if h.hunt_type == HuntType.KALSHI_MOMENTUM:
        side = str((h.details or {}).get("side", "yes"))
        return m.yes_price if side == "yes" else m.no_price
    if h.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE:
        if str((h.details or {}).get("side", "yes")) == "no":
            return max(0.01, min(0.99, 1.0 - float((h.details or {}).get("p_true", m.yes_price))))
        return float((h.details or {}).get("p_true", m.yes_price))
    return max(0.01, min(0.99, m.yes_price + scored.edge_size * 0.5))


def _price_for_kelly(m: MarketSnapshot, scored: ScoredOpportunity) -> float:
    h = _execution_primary_hunt(scored.hunts)
    d = h.details or {}
    if _has_near_zero(scored.hunts):
        return m.yes_price
    if h.hunt_type in (
        HuntType.CRYPTO_SCALP,
        HuntType.NEAR_RESOLUTION,
        HuntType.ORDER_BOOK_IMBALANCE,
        HuntType.VOLUME_SPIKE,
        HuntType.KALSHI_NEAR_CLOSE,
        HuntType.KALSHI_CONVERGENCE,
        HuntType.KALSHI_MOMENTUM,
    ):
        return m.no_price if str(d.get("side", "yes")) == "no" else m.yes_price
    if h.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE and str((h.details or {}).get("side")) == "no":
        return m.no_price
    return m.yes_price


def _arb_max_pair_usd(phase: CapitalPhase) -> float:
    if phase == CapitalPhase.PHASE_1:
        return 5.0
    if phase == CapitalPhase.PHASE_2:
        return 20.0
    return 100.0


def _hf_poly_notional(phase: CapitalPhase, capital: float, notional: float) -> float:
    """Small fixed sizes for high-frequency Polymarket hunts (crypto / near-resolution / book)."""
    if phase == CapitalPhase.PHASE_1:
        return max(1.0, min(3.0, notional))
    if phase == CapitalPhase.PHASE_2:
        return max(5.0, min(10.0, notional))
    return min(notional, capital * 0.02)


def _has_hf_poly_hunt(hunts: List) -> bool:
    return any(
        getattr(h, "hunt_type", None)
        in (
            HuntType.CRYPTO_SCALP,
            HuntType.NEAR_RESOLUTION,
            HuntType.ORDER_BOOK_IMBALANCE,
            HuntType.VOLUME_SPIKE,
        )
        for h in hunts
    )


def _build_pure_arbitrage_intent(
    scored: ScoredOpportunity,
    *,
    capital: float,
    strategy_key: str,
    outlet: str,
    gap_exploitation_mode: bool,
    current_gap_exposure_fraction: float,
    min_edge_effective: float | None,
    risk_position_multiplier: float,
    market_category: str,
    is_mana: bool,
    current_drawdown_pct: float,
    wallet_copy_trade: bool,
) -> Optional[ExecutionIntent]:
    m = scored.market
    if scored.tier == OpportunityTier.BELOW_THRESHOLD:
        return None
    phase = detect_phase(capital)
    pp = phase_params(phase)
    min_e = min_edge_effective if min_edge_effective is not None else pp.min_edge
    _ht_floor = [
        getattr(h.hunt_type, "value", str(h.hunt_type))
        for h in scored.hunts
        if getattr(h, "hunt_type", None) is not None
    ]
    _floor = ceo_sessions.get_ceo_min_edge_floor_for_hunts(_ht_floor)
    if _floor is not None:
        min_e = max(min_e, _floor)
    if scored.edge_size < min_e:
        return None
    yes_p = float(m.yes_price)
    no_p = float(m.no_price)
    pair_cost = yes_p + no_p
    if pair_cost <= 1e-9:
        return None
    max_pair = _arb_max_pair_usd(phase)
    cap_frac = min(pp.max_single_position_fraction, 0.15)
    units = min(max_pair / pair_cost, capital * cap_frac / pair_cost)
    notional = units * pair_cost
    if notional < MIN_POSITION_USD:
        return None
    notional *= risk_position_multiplier
    units = notional / pair_cost
    u = m.underlying_data_if_available or {}
    yes_tid = getattr(m, "yes_token_id", None) or u.get("yes_token_id") or u.get("token_id")
    no_tid = getattr(m, "no_token_id", None) or u.get("no_token_id")
    if not yes_tid or not no_tid:
        _log.info("Intent built: False market=%s reason=pure_arb_missing_tokens", m.market_id)
        return None
    stake = notional / max(capital, 1e-9)
    if gap_exploitation_mode:
        room = max(0.0, 0.60 - current_gap_exposure_fraction)
        stake = min(stake, room)
        notional = stake * capital
        units = notional / pair_cost
    loss_mult = LOSS_TRACKER.cluster_multiplier(
        strategy=strategy_key,
        hunt_type=HuntType.PURE_ARBITRAGE,
        outlet=outlet,
        market_category=market_category,
    )
    stake *= loss_mult
    notional = stake * capital
    units = notional / pair_cost
    margin_borrowed = 0.0
    if not is_mana:
        margin_allowance = get_margin_allowance(
            capital=capital,
            confidence=scored.confidence,
            hunt_tier=scored.tier.value,
            current_drawdown_pct=current_drawdown_pct,
            near_zero_hunt=False,
        )
        if notional > capital + 1e-9:
            if not check_margin_safety(notional, capital, margin_allowance):
                notional = min(notional, capital)
                stake = notional / max(capital, 1e-9)
                units = notional / pair_cost
        margin_borrowed = max(0.0, notional - capital)
    meta = {
        "phase": phase.value,
        "tier": scored.tier.value,
        "pure_arbitrage_dual": True,
        "yes_leg": {
            "token_id": str(yes_tid),
            "limit_price": yes_p,
            "size": float(units),
        },
        "no_leg": {
            "token_id": str(no_tid),
            "limit_price": no_p,
            "size": float(units),
        },
        "condition_id": u.get("condition_id"),
        "market_category": market_category,
        "margin_borrowed": margin_borrowed,
        "margin_cap_pct": effective_margin_pct_cap(capital, scored.confidence) if not is_mana else 0.0,
    }
    if wallet_copy_trade:
        meta["trade_type"] = "wallet_copy"
    shr = max(1, int(units))
    _log.info(
        "Intent built: True market=%s notional_usd=%.2f outlet=%s (pure_arbitrage dual)",
        m.market_id,
        notional,
        outlet,
    )
    return ExecutionIntent(
        market_id=m.market_id,
        outlet=outlet,
        side="yes",
        stake_fraction_of_capital=stake,
        edge_after_fees=scored.edge_size,
        estimated_win_probability=0.99,
        hunt_types=[HuntType.PURE_ARBITRAGE],
        source="shark_gap" if gap_exploitation_mode else "shark_compounding",
        gap_exploit=gap_exploitation_mode,
        meta=meta,
        expected_price=yes_p,
        notional_usd=notional,
        shares=shr,
        is_mana=is_mana,
    )


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
        _log.info("Intent built: False market=%s reason=below_tier", scored.market.market_id)
        return None
    m = scored.market
    if (outlet or "").strip().lower() == "polymarket":
        from trading_ai.shark.outlets.polymarket import enrich_polymarket_snapshot_tokens

        enrich_polymarket_snapshot_tokens(m)
    phase = detect_phase(capital)
    pp = phase_params(phase)
    min_e = min_edge_effective if min_edge_effective is not None else pp.min_edge
    _ht_floor_main = [
        getattr(h.hunt_type, "value", str(h.hunt_type))
        for h in scored.hunts
        if getattr(h, "hunt_type", None) is not None
    ]
    _floor_main = ceo_sessions.get_ceo_min_edge_floor_for_hunts(_ht_floor_main)
    if _floor_main is not None:
        min_e = max(min_e, _floor_main)
    if scored.edge_size < min_e:
        _log.info(
            "Intent built: False market=%s reason=edge %.4f < min %.4f",
            m.market_id,
            scored.edge_size,
            min_e,
        )
        return None
    if any(h.hunt_type == HuntType.PURE_ARBITRAGE for h in scored.hunts):
        return _build_pure_arbitrage_intent(
            scored,
            capital=capital,
            strategy_key=strategy_key,
            outlet=outlet,
            gap_exploitation_mode=gap_exploitation_mode,
            current_gap_exposure_fraction=current_gap_exposure_fraction,
            min_edge_effective=min_edge_effective,
            risk_position_multiplier=risk_position_multiplier,
            market_category=market_category,
            is_mana=is_mana,
            current_drawdown_pct=current_drawdown_pct,
            wallet_copy_trade=wallet_copy_trade,
        )
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
    primary_ht = _execution_primary_hunt(scored.hunts).hunt_type
    loss_mult = LOSS_TRACKER.cluster_multiplier(
        strategy=strategy_key,
        hunt_type=primary_ht,
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
    if 0.0 < notional < MIN_POSITION_USD:
        notional = MIN_POSITION_USD
        stake = min(notional / max(capital, 1e-9), pp.max_single_position_fraction)
    if (
        _has_hf_poly_hunt(scored.hunts)
        and (outlet or "").strip().lower() == "polymarket"
    ):
        notional = _hf_poly_notional(phase, capital, notional)
        stake = min(notional / max(capital, 1e-9), pp.max_single_position_fraction)
    if (outlet or "").strip().lower() == "kalshi":
        from trading_ai.shark import kalshi_limits

        lo, hi = kalshi_limits.kalshi_notional_bounds_usd()
        notional = min(hi, max(lo, notional))
        stake = min(notional / max(capital, 1e-9), pp.max_single_position_fraction)
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
    yes_tid = getattr(m, "yes_token_id", None) or u.get("yes_token_id") or u.get("token_id")
    no_tid = getattr(m, "no_token_id", None) or u.get("no_token_id")
    tok = no_tid if side == "no" else yes_tid
    meta = {
        "phase": phase.value,
        "tier": scored.tier.value,
        "token_id": tok,
        "yes_token_id": yes_tid,
        "no_token_id": no_tid,
        "condition_id": u.get("condition_id"),
        "market_category": market_category,
        "margin_borrowed": margin_borrowed,
        "margin_cap_pct": effective_margin_pct_cap(capital, scored.confidence) if not is_mana else 0.0,
    }
    if wallet_copy_trade:
        meta["trade_type"] = "wallet_copy"
    if (outlet or "").strip().lower() == "polymarket":
        from trading_ai.shark.outlets.polymarket import _token_id_log_preview as _tidpv

        _log.info(
            "Polymarket intent tokens: yes=%s no=%s market=%s",
            _tidpv(str(yes_tid) if yes_tid else None) or "MISSING",
            _tidpv(str(no_tid) if no_tid else None) or "MISSING",
            m.market_id,
        )
    _log.info(
        "Intent built: True market=%s notional_usd=%.2f outlet=%s",
        m.market_id,
        notional,
        outlet,
    )
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
