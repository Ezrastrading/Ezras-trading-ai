"""
Gate B momentum engine — wires liquidity gate, breakout filter, ranking, regime, correlation,
re-entry, data quality, execution reality summaries, and exit priority (exit faster than entry).

This module is pure orchestration + deterministic helpers; live I/O stays in callers (feeds, orders).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from trading_ai.shark.lesson_runtime_influence import (
    apply_lessons_to_exit_params,
    apply_lessons_to_gate_b_evaluation,
    write_lessons_runtime_effect,
)

from trading_ai.shark.coinbase_spot.breakout_filter import evaluate_breakout_entry
from trading_ai.shark.coinbase_spot.capital_allocation import (
    GateAllocationSplit,
    compute_gate_allocation_split,
    gate_b_position_budgets_usd,
)
from trading_ai.shark.coinbase_spot.execution_reality import infer_asset_tier, simulate_execution_prices
from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig, load_gate_b_config_from_env
from trading_ai.shark.coinbase_spot.gate_b_correlation import evaluate_portfolio_correlation
from trading_ai.shark.coinbase_spot.gate_b_data_quality import evaluate_data_quality
from trading_ai.shark.coinbase_spot.gate_b_edge_stats import GateBEdgeStats
from trading_ai.shark.coinbase_spot.gate_b_events import detect_sudden_move
from trading_ai.shark.coinbase_spot.gate_b_monitor import GateBMonitorState, gate_b_monitor_tick
from trading_ai.shark.coinbase_spot.gate_b_regime import detect_regime
from trading_ai.shark.coinbase_spot.gate_b_reentry import ReentryController
from trading_ai.shark.coinbase_spot.gate_b_scanner import (
    GateBCandidate,
    gate_b_candidates_from_scan,
    gate_b_momentum_scan,
    rank_gate_b_candidates,
)
from trading_ai.shark.coinbase_spot.gate_b_truth import (
    failure_codes_for_breakout,
    failure_codes_for_correlation,
    failure_codes_for_data_quality,
    failure_codes_for_liquidity,
    failure_codes_for_reentry,
    GATE_B_TRUTH_MODEL_VERSION,
)
from trading_ai.shark.coinbase_spot.liquidity_gate import evaluate_liquidity_gate


@dataclass
class GateBMomentumEngine:
    """
    Holds configurable state for scans. Call :meth:`evaluate_entry_candidates` with market rows
    and :meth:`evaluate_exits` with open monitor states + prices.
    """

    config: GateBConfig = field(default_factory=load_gate_b_config_from_env)
    allocation: GateAllocationSplit = field(default_factory=compute_gate_allocation_split)
    reentry: ReentryController = field(default_factory=ReentryController)
    edge_stats: GateBEdgeStats = field(default_factory=GateBEdgeStats)

    def __post_init__(self) -> None:
        self.reentry.cooldown_sec = float(self.config.reentry_cooldown_sec)

    def evaluate_entry_candidates(
        self,
        raw_rows: Sequence[Mapping[str, Any]],
        *,
        open_product_ids: Sequence[str],
        regime_inputs: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Filter → enrich → rank. Does not place orders."""
        regime = detect_regime(**dict(regime_inputs or {}))
        if regime["regime"] == "chop" and self.config.disable_gate_b_in_chop:
            return {
                "candidates": [],
                "rejected": [],
                "regime": regime,
                "gate_b_disabled": True,
                "reason": "chop_regime",
                "pre_rank_rejections": [],
                "pre_rank_rejection_count": 0,
                "gate_b_truth_version": GATE_B_TRUTH_MODEL_VERSION,
            }

        if len(list(open_product_ids)) >= int(self.config.max_simultaneous_positions):
            return {
                "candidates": [],
                "rejected": [],
                "regime": regime,
                "gate_b_full": True,
                "reason": "max_open_positions",
                "edge": self.edge_stats.report(),
                "pre_rank_rejections": [],
                "pre_rank_rejection_count": 0,
                "gate_b_truth_version": GATE_B_TRUTH_MODEL_VERSION,
            }

        enriched: List[Dict[str, Any]] = []
        pre_rank_rejections: List[Dict[str, Any]] = []
        for r in raw_rows:
            if not isinstance(r, dict):
                continue
            pid = str(r.get("product_id") or "").strip().upper()
            if not pid:
                continue
            def _learn(stage: str, *, passed: bool, rejection_kind: Optional[str] = None, failure_codes: Optional[List[str]] = None, detail: Optional[Mapping[str, Any]] = None) -> None:
                try:
                    from trading_ai.intelligence.crypto_intelligence.recorder import record_gate_b_candidate_event

                    record_gate_b_candidate_event(
                        runtime_root=None,
                        row=r,
                        stage=stage,
                        passed=passed,
                        rejection_kind=rejection_kind,
                        failure_codes=failure_codes,
                        detail=detail,
                    )
                except Exception:
                    return
            dq = evaluate_data_quality(
                quote_ts=r.get("quote_ts"),
                max_age_sec=float(self.config.max_quote_age_sec),
                bid=r.get("best_bid"),
                ask=r.get("best_ask"),
            )
            if not dq["acceptable"]:
                _learn(
                    "data_quality",
                    passed=False,
                    rejection_kind="data_quality",
                    failure_codes=failure_codes_for_data_quality(dq),
                    detail=dq,
                )
                pre_rank_rejections.append(
                    {
                        "product_id": pid,
                        "stage": "data_quality",
                        "candidate_seen": True,
                        "candidate_evaluable": True,
                        "candidate_passed": False,
                        "failure_codes": failure_codes_for_data_quality(dq),
                        "rejection_kind": "data_quality",
                        "detail": dq,
                    }
                )
                enriched.append({**dict(r), "_dq_fail": dq})
                continue
            liq = evaluate_liquidity_gate(
                r,
                min_volume_24h_usd=float(self.config.min_volume_24h_usd),
                max_spread_bps=float(self.config.max_spread_bps),
                min_depth_usd=float(self.config.min_book_depth_usd),
            )
            if not liq["passed"]:
                _learn(
                    "liquidity_gate",
                    passed=False,
                    rejection_kind="market_policy",
                    failure_codes=failure_codes_for_liquidity(liq),
                    detail={k: v for k, v in liq.items() if k != "components"},
                )
                pre_rank_rejections.append(
                    {
                        "product_id": pid,
                        "stage": "liquidity_gate",
                        "candidate_seen": True,
                        "candidate_evaluable": True,
                        "candidate_passed": False,
                        "failure_codes": failure_codes_for_liquidity(liq),
                        "rejection_kind": "market_policy",
                        "detail": {k: v for k, v in liq.items() if k != "components"},
                    }
                )
                enriched.append({**dict(r), "_liq": liq})
                continue
            br = evaluate_breakout_entry(
                r,
                min_move_pct=float(self.config.min_breakout_move_pct),
                min_volume_surge_ratio=float(self.config.min_volume_surge_ratio),
                min_continuation_candles=int(self.config.min_continuation_candles),
                min_momentum_score=float(self.config.min_momentum_score_entry),
            )
            if not br["passed"]:
                _learn(
                    "breakout_filter",
                    passed=False,
                    rejection_kind="market_policy",
                    failure_codes=failure_codes_for_breakout(br),
                    detail=br,
                )
                pre_rank_rejections.append(
                    {
                        "product_id": pid,
                        "stage": "breakout_filter",
                        "candidate_seen": True,
                        "candidate_evaluable": True,
                        "candidate_passed": False,
                        "failure_codes": failure_codes_for_breakout(br),
                        "rejection_kind": "market_policy",
                        "detail": br,
                    }
                )
                enriched.append({**dict(r), "_breakout": br})
                continue
            tier = infer_asset_tier(pid, liquidity_score=liq["liquidity_score"])
            ms = liq.get("measured_spread_bps")
            row_for_rank = {
                "product_id": pid,
                "momentum_score": br["momentum_score"],
                "liquidity_score": liq["liquidity_score"],
                "exhaustion_risk": float(r.get("exhaustion_risk") or 0.0),
                "spread_bps": float(ms) if ms is not None else float(r.get("spread_bps") or 0.0),
                "volume_accel": r.get("volume_accel"),
                "_tier": tier.value,
                "_data_quality": dq["data_quality_score"],
                "_spread_measurement_status": liq.get("spread_measurement_status"),
                "_measured_spread_bps": ms,
            }
            corr = evaluate_portfolio_correlation(
                list(open_product_ids),
                proposed_product_id=pid,
                max_high_corr=int(self.config.max_high_corr_positions),
            )
            if not corr["allowed"]:
                _learn(
                    "portfolio_correlation",
                    passed=False,
                    rejection_kind="market_policy",
                    failure_codes=failure_codes_for_correlation(corr),
                    detail=corr,
                )
                pre_rank_rejections.append(
                    {
                        "product_id": pid,
                        "stage": "portfolio_correlation",
                        "candidate_seen": True,
                        "candidate_evaluable": True,
                        "candidate_passed": False,
                        "failure_codes": failure_codes_for_correlation(corr),
                        "rejection_kind": "market_policy",
                        "detail": corr,
                    }
                )
                enriched.append({**dict(r), "_corr": corr})
                continue
            re_ok, re_rs = self.reentry.can_reenter(
                pid,
                momentum_score=float(br["momentum_score"]),
                new_breakout_confirmed=bool(r.get("new_breakout_confirmed", True)),
            )
            if not re_ok:
                _learn(
                    "reentry",
                    passed=False,
                    rejection_kind="market_policy",
                    failure_codes=failure_codes_for_reentry(re_rs),
                    detail={"reasons": re_rs},
                )
                pre_rank_rejections.append(
                    {
                        "product_id": pid,
                        "stage": "reentry",
                        "candidate_seen": True,
                        "candidate_evaluable": True,
                        "candidate_passed": False,
                        "failure_codes": failure_codes_for_reentry(re_rs),
                        "rejection_kind": "market_policy",
                        "detail": {"reasons": re_rs},
                    }
                )
                enriched.append({**dict(r), "_reentry": re_rs})
                continue
            _learn("passed_pre_rank", passed=True, detail={"momentum_score": br.get("momentum_score"), "liquidity_score": liq.get("liquidity_score")})
            enriched.append({**row_for_rank, "_row": dict(r)})

        rank_rows = [x for x in enriched if "product_id" in x and "_row" in x]
        slots = max(0, int(self.config.max_simultaneous_positions) - len(list(open_product_ids)))
        use_momentum = any(
            len((x.get("_row") or {}).get("closes") or []) >= 10 for x in rank_rows
        )
        if use_momentum and rank_rows:
            mom_rows: List[Dict[str, Any]] = []
            for x in rank_rows:
                base = dict(x.get("_row") or {})
                base["product_id"] = x["product_id"]
                mom_rows.append(base)
            scan = gate_b_momentum_scan(
                mom_rows,
                base_threshold=float(self.config.momentum_threshold_0_100),
                top_k=max(1, min(int(self.config.momentum_top_k), slots or 1)),
            )
            acc, rej = gate_b_candidates_from_scan(
                scan,
                max_results=max(1, slots),
                min_liquidity_0_1=float(self.config.min_liquidity_score),
            )
        else:
            acc, rej = rank_gate_b_candidates(
                rank_rows,
                min_liquidity_score=float(self.config.min_liquidity_score),
                min_momentum_score=float(self.config.min_momentum_score),
                max_results=max(1, slots),
            )
        try:
            # Record accepted + not-selected outcomes (advisory learning only).
            from trading_ai.intelligence.crypto_intelligence.recorder import record_gate_b_candidate_event
            for c in list(acc or [])[: max(0, int(self.config.max_simultaneous_positions))]:
                pid = str(getattr(c, "product_id", "") or "").strip().upper()
                rr = next((x for x in raw_rows if isinstance(x, Mapping) and str(x.get("product_id") or "").strip().upper() == pid), None)
                if isinstance(rr, Mapping):
                    record_gate_b_candidate_event(runtime_root=None, row=rr, stage="accepted", passed=True, detail={"score": getattr(c, "score", None)})
            for rj in list(rej or [])[:80]:
                pid = str(rj.get("product_id") or "").strip().upper()
                rr = next((x for x in raw_rows if isinstance(x, Mapping) and str(x.get("product_id") or "").strip().upper() == pid), None)
                if isinstance(rr, Mapping):
                    record_gate_b_candidate_event(runtime_root=None, row=rr, stage="rank_rejected", passed=False, rejection_kind="rank", failure_codes=["not_selected"], detail=rj)
        except Exception:
            pass
        mult = float(regime["gate_b_size_multiplier"])
        edge_rep = self.edge_stats.report()
        if edge_rep.get("recommend_reduce_size"):
            mult *= 0.6
        if edge_rep.get("recommend_pause_gate_b"):
            acc = []
        out: Dict[str, Any] = {
            "candidates": [self._attach_sizing(c, mult) for c in acc],
            "rejected_ranker": rej,
            "pre_rank_rejections": pre_rank_rejections,
            "pre_rank_rejection_count": len(pre_rank_rejections),
            "regime": regime,
            "size_multiplier": mult,
            "edge": edge_rep,
            "gate_b_truth_version": GATE_B_TRUTH_MODEL_VERSION,
        }
        if use_momentum and rank_rows:
            out["momentum_scan"] = {
                "effective_threshold": scan.effective_threshold,
                "market_strength_0_1": scan.market_strength_0_1,
                "selected_product_ids": list(scan.selected_product_ids),
                "weights_used": list(scan.weights_used),
            }
        try:
            apply_lessons_to_gate_b_evaluation(out)
            write_lessons_runtime_effect()
        except Exception:
            pass
        out["liquidity_and_stability_provenance_summary"] = {
            "liquidity_fields": "volume_24h_usd / spread_bps / book_depth_usd are row-supplied; see liquidity_gate.field_provenance",
            "quote_freshness": "evaluate_data_quality uses quote_ts and bid/ask from rows; see gate_b_data_quality.field_provenance",
            "honesty_note": (
                "This engine does not pull a venue-wide consolidated feed; caller-supplied rows must self-label or remain lower-confidence."
            ),
        }
        return out

    def _attach_sizing(self, c: GateBCandidate, regime_mult: float) -> Dict[str, Any]:
        out = {
            "product_id": c.product_id,
            "score": c.score,
            "momentum_score": c.momentum_score,
            "liquidity_score": c.liquidity_score,
            "sizing_note": "use gate_b_position_budgets_usd with total_gate_b_usd * regime_mult",
            "regime_mult": regime_mult,
        }
        if c.momentum_score_0_100 > 0:
            out["momentum_score_0_100"] = c.momentum_score_0_100
            out["component_scores"] = c.component_scores
        return out

    def evaluate_exits(
        self,
        positions: List[GateBMonitorState],
        *,
        price_by_product: Mapping[str, float],
        prev_price_by_product: Optional[Mapping[str, float]] = None,
        now_ts: float,
    ) -> List[Dict[str, Any]]:
        """Exit loop (faster cadence than entry) — includes sudden-move priority."""
        prev = dict(prev_price_by_product or {})
        out: List[Dict[str, Any]] = []
        for st in positions:
            pid = st.product_id
            pt_adj, tr_adj, hs_adj, _les = apply_lessons_to_exit_params(self.config, pid)
            eff_profit = max(
                1e-9,
                float(pt_adj) - float(self.config.profit_exit_slippage_buffer_pct),
            )
            last = float(price_by_product.get(pid) or 0.0)
            ev = detect_sudden_move(
                last_price=last,
                prev_price=prev.get(pid),
                sudden_drop_pct=float(self.config.sudden_drop_exit_pct),
                sudden_spike_pct=float(self.config.sudden_spike_review_pct),
            )
            st.observe_price(last, now_ts)
            if ev.get("sudden_drop"):
                out.append(
                    {
                        "product_id": pid,
                        "exit": True,
                        "exit_reason": "sudden_drop_event",
                        "event": ev,
                    }
                )
                continue
            tick = gate_b_monitor_tick(
                st,
                now_ts=now_ts,
                profit_target_pct=eff_profit,
                trailing_stop_from_peak_pct=float(tr_adj),
                hard_stop_from_entry_pct=float(hs_adj),
                max_hold_sec=float(self.config.max_hold_sec),
            )
            tick["product_id"] = pid
            tick["event"] = ev
            out.append(tick)
        if out:
            try:
                write_lessons_runtime_effect()
            except Exception:
                pass
        return out

    def record_closed_trade_pnl(self, net_pnl_usd: float, *, product_id: Optional[str] = None) -> None:
        self.edge_stats.record_trade_net_pnl(net_pnl_usd)
        if product_id:
            from trading_ai.shark.lesson_runtime_influence import record_negative_lesson_for_rebuy

            record_negative_lesson_for_rebuy(self.reentry, product_id, net_pnl_usd=net_pnl_usd)


def demo_execution_summary(
    *,
    product_id: str,
    intended_entry: float,
    intended_exit: float,
    base_qty: float,
    fees_usd: float,
    liquidity_score: float,
) -> Dict[str, Any]:
    """Attach to trade record — theoretical vs actual using midpoint slippage model."""
    from trading_ai.shark.coinbase_spot.execution_reality import theoretical_vs_actual_roundtrip_pnl_usd

    tier = infer_asset_tier(product_id, liquidity_score=liquidity_score)
    sim = simulate_execution_prices(
        intended_entry_price=intended_entry,
        intended_exit_price=intended_exit,
        tier=tier,
    )
    pnl = theoretical_vs_actual_roundtrip_pnl_usd(
        base_qty=base_qty,
        intended_entry=intended_entry,
        intended_exit=intended_exit,
        actual_entry=sim["actual_entry_price"],
        actual_exit=sim["actual_exit_price"],
        fees_usd=fees_usd,
    )
    return {**sim, **pnl}
