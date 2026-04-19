"""A/B live router: mean reversion vs continuation; C sandbox-only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from trading_ai.nte.config.coinbase_avenue1_launch import (
    CoinbaseAvenue1Launch,
    entry_offset_bps,
    load_coinbase_avenue1_launch,
)
from trading_ai.nte.data.feature_engine import FeatureSnapshot
from trading_ai.nte.execution.net_edge_gate import evaluate_net_edge, rough_expected_move_bps_from_z
from trading_ai.nte.strategies.signals import StrategySignal

logger = logging.getLogger(__name__)


@dataclass
class RouterDecision:
    chosen: Optional[StrategySignal]
    score_a: float
    score_b: float
    score_c: float
    rejected: List[str]
    net_edge_bps: float
    spread_bps: float
    est_round_trip_cost_bps: float
    estimated_maker_fee_pct: float
    estimated_taker_fee_pct: float
    expected_move_bps: float
    vol_regime: str
    router_reason: str
    position_base_pct: float
    position_max_pct: float
    stale_pending_seconds: float
    strategy_params: Dict[str, Any]


def _scores(store: Any) -> Dict[str, Any]:
    ss = store.load_json("strategy_scores.json")
    avenues = ss.get("avenues") or {}
    cb = avenues.get("coinbase") or {}
    return cb if isinstance(cb, dict) else {}


def _base_score(raw_confidence: float, w_store: float, launch_weight: float) -> float:
    return min(0.99, max(0.0, raw_confidence * (0.5 + w_store) * launch_weight))


def pick_live_route(
    feat: FeatureSnapshot,
    store: Any,
    launch: Optional[CoinbaseAvenue1Launch] = None,
    *,
    short_vol_bps: Optional[float] = None,
) -> Optional[RouterDecision]:
    launch = launch or load_coinbase_avenue1_launch()
    rejected: List[str] = []
    wb = _scores(store)
    mr_w = float((wb.get("mean_reversion") or {}).get("score") or 0.5)
    cp_w = float((wb.get("continuation_pullback") or {}).get("score") or 0.5)
    mm_w = float((wb.get("micro_momentum") or {}).get("score") or 0.5)

    a_cfg = launch.strategy_a
    b_cfg = launch.strategy_b
    c_cfg = launch.strategy_c
    pid = feat.product_id
    spread_bps = feat.spread_pct * 10000.0

    # --- A
    score_a = 0.0
    if a_cfg.enabled_live and feat.regime == "range" and feat.z_score < -a_cfg.zscore_entry_threshold:
        raw = min(0.95, 0.55 + mr_w * 0.2)
        score_a = _base_score(raw, mr_w, launch.router.a_weight)
    else:
        rejected.append("A:not_triggered")

    cap_spread_a = a_cfg.max_spread_bps_btc if "BTC" in pid.upper() else a_cfg.max_spread_bps_eth
    cap_vol_a = a_cfg.max_short_vol_bps_btc if "BTC" in pid.upper() else a_cfg.max_short_vol_bps_eth
    if score_a > 0 and spread_bps > cap_spread_a:
        rejected.append("A:spread_cap")
        score_a = 0.0
    if score_a > 0 and short_vol_bps is not None and short_vol_bps > cap_vol_a:
        rejected.append("A:vol_cap")
        score_a = 0.0

    # --- B
    score_b = 0.0
    if b_cfg.enabled_live and feat.regime == "trend_up" and -0.35 < feat.z_score < 0.15:
        raw = min(0.92, 0.52 + cp_w * 0.22)
        score_b = _base_score(raw, cp_w, launch.router.b_weight)
    else:
        rejected.append("B:not_triggered")

    cap_spread_b = b_cfg.max_spread_bps_btc if "BTC" in pid.upper() else b_cfg.max_spread_bps_eth
    cap_vol_b = b_cfg.max_short_vol_bps_btc if "BTC" in pid.upper() else b_cfg.max_short_vol_bps_eth
    if score_b > 0 and spread_bps > cap_spread_b:
        rejected.append("B:spread_cap")
        score_b = 0.0
    if score_b > 0 and short_vol_bps is not None and short_vol_bps > cap_vol_b:
        rejected.append("B:vol_cap")
        score_b = 0.0

    # --- C (sandbox log only)
    score_c = 0.0
    if (
        c_cfg.sandbox_only
        and feat.regime in ("trend_up", "trend_down")
        and abs(feat.z_score) < 0.45
        and spread_bps <= c_cfg.max_spread_bps
    ):
        raw = min(0.88, 0.48 + mm_w * 0.25)
        score_c = raw
    if c_cfg.record_candidate_signals and score_c >= c_cfg.candidate_score_min:
        logger.info(
            "NTE sandbox C micro_momentum pid=%s score_c=%.3f (not live)",
            pid,
            score_c,
        )

    if score_a <= 0 and score_b <= 0:
        _log_eval(pid, score_a, score_b, None, rejected, 0.0, spread_bps, feat.regime, "no_ab_candidate")
        return None

    # Selection: max score; tie band -> prefer A; borderline noisy -> prefer A if A exists
    tie = launch.router.prefer_a_if_score_difference_under
    if score_a > 0 and score_b > 0:
        if abs(score_a - score_b) <= tie:
            best_name, best_s, cfg = "mean_reversion", score_a, a_cfg
            router_reason = "tie_prefer_A"
        elif score_a >= score_b:
            best_name, best_s, cfg = "mean_reversion", score_a, a_cfg
            router_reason = "A_higher"
        else:
            best_name, best_s, cfg = "continuation_pullback", score_b, b_cfg
            router_reason = "B_higher"
    elif score_a > 0:
        best_name, best_s, cfg = "mean_reversion", score_a, a_cfg
        router_reason = "A_only"
    else:
        best_name, best_s, cfg = "continuation_pullback", score_b, b_cfg
        router_reason = "B_only"

    if (
        launch.router.no_trade_if_both_below_threshold
        and score_a > 0
        and score_b > 0
        and score_a < 0.45
        and score_b < 0.45
    ):
        rejected.append("both_below_soft_threshold")
        _log_eval(pid, score_a, score_b, None, rejected, 0.0, spread_bps, feat.regime, "both_weak")
        return None

    # Borderline noisy: prefer A when both exist
    if not feat.stable and score_a > 0:
        best_name, best_s, cfg = "mean_reversion", score_a, a_cfg
        router_reason = "unstable_prefer_A"

    min_edge = (
        a_cfg.min_net_edge_bps_after_cost
        if best_name == "mean_reversion"
        else b_cfg.min_net_edge_bps_after_cost
    )
    exp_move = rough_expected_move_bps_from_z(feat.z_score)
    edge = evaluate_net_edge(
        spread_pct=feat.spread_pct,
        expected_edge_bps=exp_move,
        strategy_min_net_bps=min_edge,
        launch=launch,
        assume_maker_entry=True,
    )
    if launch.router.require_post_fee_positive_expectancy and not edge.allowed:
        rejected.append(f"net_edge:{edge.reason}")
        _log_eval(
            pid,
            score_a,
            score_b,
            None,
            rejected,
            edge.expected_net_edge_bps,
            spread_bps,
            feat.regime,
            "net_edge_fail",
        )
        return None

    sig = StrategySignal(best_name, f"router:{router_reason}", float(best_s))
    dec = RouterDecision(
        chosen=sig,
        score_a=score_a,
        score_b=score_b,
        score_c=score_c,
        rejected=rejected,
        net_edge_bps=edge.expected_net_edge_bps,
        spread_bps=spread_bps,
        est_round_trip_cost_bps=edge.est_round_trip_cost_bps,
        estimated_maker_fee_pct=launch.fees.estimated_maker_fee_pct,
        estimated_taker_fee_pct=launch.fees.estimated_taker_fee_pct,
        expected_move_bps=exp_move,
        vol_regime=feat.regime,
        router_reason=router_reason,
        position_base_pct=cfg.base_position_pct_equity,
        position_max_pct=cfg.max_position_pct_equity,
        stale_pending_seconds=float(
            a_cfg.stale_pending_seconds
            if best_name == "mean_reversion"
            else b_cfg.stale_pending_seconds
        ),
        strategy_params={"entry_offset_bps": entry_offset_bps(pid, best_name, launch)},
    )
    _log_eval(
        pid,
        score_a,
        score_b,
        best_name,
        rejected,
        edge.expected_net_edge_bps,
        spread_bps,
        feat.regime,
        router_reason,
    )
    return dec


def _log_eval(
    pid: str,
    sa: float,
    sb: float,
    chosen: Optional[str],
    rejected: List[str],
    net_edge: float,
    spread_bps: float,
    regime: str,
    reason: str,
) -> None:
    logger.info(
        "NTE router eval %s A=%.3f B=%.3f chosen=%s net_edge_bps=%.1f spread_bps=%.1f regime=%s reason=%s rejected=%s",
        pid,
        sa,
        sb,
        chosen,
        net_edge,
        spread_bps,
        regime,
        reason,
        ";".join(rejected[-6:]),
    )
