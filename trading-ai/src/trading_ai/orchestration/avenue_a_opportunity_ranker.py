"""
Avenue A cross-gate opportunity ranker (Gate A vs Gate B vs no-trade).

Evidence-first contract:
- Build candidate opportunities for Gate A + Gate B
- Detect edge explicitly (production edges)
- Estimate fee + slippage + spread costs
- Compute net expected edge bps
- Run strict profit enforcement (hard gate)
- Rank across gates and choose best (or no-trade)

This module does NOT place orders. It emits a truth artifact consumed by the daemon.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.nte.config.coinbase_avenue1_launch import load_coinbase_avenue1_launch
from trading_ai.nte.data.feature_engine import compute_features
from trading_ai.nte.execution.net_edge_gate import estimate_round_trip_cost_bps
from trading_ai.nte.execution.profit_enforcement import (
    ProfitEnforcementConfig,
    evaluate_profit_enforcement,
    profit_enforcement_allows_or_reason,
)
from trading_ai.nte.execution.edge_governance import (
    choose_best_edge,
    detect_gate_a_edges,
    detect_gate_b_edges,
)
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.orchestration.coinbase_gate_selection.coinbase_capital_split import compute_coinbase_gate_capital_split
from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import run_gate_a_product_selection
from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import run_gate_b_gainers_selection
from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp01(x: float) -> float:
    if not math.isfinite(x):
        return 0.0
    return max(0.0, min(1.0, float(x)))


@dataclass(frozen=True)
class RankedOpportunity:
    gate_id: str  # "gate_a" | "gate_b"
    product_id: str
    edge_family: str
    confidence: float
    spread_bps: float
    fee_bps_round_trip: float
    slippage_bps_round_trip: float
    expected_move_bps: float
    expected_risk_bps: float
    net_expected_edge_bps: float
    profit_enforcement_allowed: bool
    profit_enforcement_reason: str
    score: float
    notes: Dict[str, Any]


def _write_truth(runtime_root: Path, payload: Dict[str, Any]) -> None:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/opportunity_ranking_truth.json", payload)
    ad.write_text(
        "data/control/opportunity_ranking_truth.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )


def _market_memory_closes(store: MemoryStore, product_id: str) -> List[float]:
    mm = store.load_json("market_memory.json") or {}
    if not isinstance(mm, dict):
        return []
    closes = mm.get("closes")
    if not isinstance(closes, dict):
        return []
    arr = closes.get(product_id)
    if not isinstance(arr, list):
        return []
    out = []
    for x in arr[-120:]:
        if isinstance(x, (int, float)):
            out.append(float(x))
    return out


def _rank_gate_a(
    *,
    runtime_root: Path,
    client: Any,
    quote_usd_budget: float,
    anchored_majors_only: bool,
) -> Tuple[Optional[RankedOpportunity], Dict[str, Any]]:
    diag: Dict[str, Any] = {"gate_id": "gate_a"}
    sel = run_gate_a_product_selection(
        runtime_root=runtime_root,
        client=client,
        quote_usd=float(quote_usd_budget),
        explicit_product_id=None,
        anchored_majors_only=bool(anchored_majors_only),
    )
    diag["gate_a_selection_snapshot"] = sel
    pid = str((sel or {}).get("selected_product") or "").strip().upper()
    if not pid:
        diag["blocked_reason"] = "blocked_no_gate_a_selection"
        return None, diag

    store = MemoryStore()
    store.ensure_defaults()
    feat = compute_features(
        store=store,
        client=client,
        product_id=pid,
        spike_block_pct=float(os.environ.get("NTE_SPIKE_BLOCK_PCT") or 0.04),
        min_quote_volume_24h=float(os.environ.get("NTE_MIN_QUOTE_VOLUME_24H") or 50_000),
    )
    if feat is None:
        diag["blocked_reason"] = "blocked_missing_market_snapshot"
        return None, diag

    closes = _market_memory_closes(store, pid)
    edges = detect_gate_a_edges(
        closes=closes,
        feat={
            "mid": float(feat.mid),
            "spread_pct": float(feat.spread_pct),
            "z_score": float(feat.z_score),
            "regime": str(feat.regime),
            "quote_volume_24h": float(feat.quote_volume_24h or 0.0),
        },
    )
    best = choose_best_edge(
        gate_id="gate_a",
        edges=edges,
        min_confidence=float(os.environ.get("EZRAS_PROD_REQUIRED_CONFIDENCE") or 0.62),
    )
    if best is None or not best.detected:
        diag["blocked_reason"] = "blocked_no_edge"
        diag["edge_candidates"] = [e.__dict__ for e in edges]
        return None, diag

    spread_bps = float(feat.spread_pct) * 10_000.0
    launch = load_coinbase_avenue1_launch()
    fee_bps = estimate_round_trip_cost_bps(
        spread_bps=0.0,
        maker_fee_pct=float(launch.fees.estimated_maker_fee_pct),
        taker_fee_pct=float(launch.fees.estimated_taker_fee_pct),
        assume_maker_entry=True,
    )
    slip_bps = float(os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 10.0)

    net_edge_bps = float(best.expected_move_bps) - (float(spread_bps) + float(fee_bps) + float(slip_bps))
    cfg = ProfitEnforcementConfig(
        min_expected_net_edge_bps=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_EDGE_BPS") or 2.0),
        min_expected_net_pnl_usd=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_PNL_USD") or 0.05),
        min_reward_to_risk=float(os.environ.get("EZRAS_MIN_REWARD_TO_RISK") or 1.10),
        slippage_buffer_bps=float(slip_bps),
    )
    pe = evaluate_profit_enforcement(
        runtime_root=runtime_root,
        trade_id=f"rank_gate_a_{pid}",
        avenue_id="A",
        gate_id="gate_a",
        product_id=pid,
        quote_usd=float(quote_usd_budget),
        spread_bps=float(spread_bps),
        fee_bps_round_trip=float(fee_bps),
        expected_gross_move_bps=float(best.expected_move_bps),
        expected_risk_bps=float(best.expected_risk_bps or max(1.0, float(best.expected_move_bps) / 1.2)),
        config=cfg,
        extra={"surface": "avenue_a_opportunity_ranker", "edge_family": best.edge_type.value, "confidence": best.edge_confidence},
        write_artifact=True,
    )
    ok_pe, why_pe = profit_enforcement_allows_or_reason(pe)

    # Score: net edge * confidence with modest penalty for wide spreads.
    spread_pen = max(0.0, (spread_bps - 20.0) / 40.0)  # 0..?
    score = float(net_edge_bps) * float(_clamp01(best.edge_confidence)) * (1.0 - 0.25 * _clamp01(spread_pen))

    return (
        RankedOpportunity(
            gate_id="gate_a",
            product_id=pid,
            edge_family=best.edge_type.value,
            confidence=float(best.edge_confidence),
            spread_bps=float(spread_bps),
            fee_bps_round_trip=float(fee_bps),
            slippage_bps_round_trip=float(slip_bps),
            expected_move_bps=float(best.expected_move_bps),
            expected_risk_bps=float(best.expected_risk_bps),
            net_expected_edge_bps=float(net_edge_bps),
            profit_enforcement_allowed=bool(ok_pe),
            profit_enforcement_reason=str(why_pe),
            score=float(score),
            notes={"profit_enforcement": pe, "edge_reason": best.reason},
        ),
        diag,
    )


def _rank_gate_b(
    *,
    runtime_root: Path,
    client: Any,
    quote_usd_budget: float,
) -> Tuple[Optional[RankedOpportunity], Dict[str, Any]]:
    diag: Dict[str, Any] = {"gate_id": "gate_b"}
    sel = run_gate_b_gainers_selection(
        runtime_root=runtime_root,
        client=client,
        deployable_quote_usd=float(quote_usd_budget),
    )
    diag["gate_b_selection_snapshot"] = sel
    rows = (sel or {}).get("ranked_gainer_candidates")
    if not isinstance(rows, list) or not rows:
        diag["blocked_reason"] = "blocked_no_gate_b_candidates"
        return None, diag

    # Choose first passing candidate and compute edge.
    chosen_row = None
    for r in rows:
        if isinstance(r, dict) and str(r.get("product_id") or "").strip():
            if bool(r.get("passed")):
                chosen_row = r
                break
    if chosen_row is None:
        diag["blocked_reason"] = "blocked_no_gate_b_candidate_passed_policy"
        return None, diag

    pid = str(chosen_row.get("product_id") or "").strip().upper()
    edges = detect_gate_b_edges(row=dict(chosen_row))
    best = choose_best_edge(
        gate_id="gate_b",
        edges=edges,
        min_confidence=float(os.environ.get("EZRAS_GATE_B_REQUIRED_CONFIDENCE") or 0.66),
    )
    if best is None or not best.detected:
        diag["blocked_reason"] = "blocked_no_edge"
        diag["edge_candidates"] = [e.__dict__ for e in edges]
        return None, diag

    # Conservative: assume taker+taker round-trip for gainers lane.
    launch = load_coinbase_avenue1_launch()
    fee_bps = estimate_round_trip_cost_bps(
        spread_bps=0.0,
        maker_fee_pct=float(launch.fees.estimated_maker_fee_pct),
        taker_fee_pct=float(launch.fees.estimated_taker_fee_pct),
        assume_maker_entry=False,
    )
    spread_bps = float(chosen_row.get("measured_spread_bps") or chosen_row.get("spread_bps") or 0.0)
    slip_bps = float(os.environ.get("EZRAS_GATE_B_SLIPPAGE_BUFFER_BPS") or os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 12.0)

    net_edge_bps = float(best.expected_move_bps) - (float(spread_bps) + float(fee_bps) + float(slip_bps))
    cfg = ProfitEnforcementConfig(
        min_expected_net_edge_bps=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_EDGE_BPS") or 2.0),
        min_expected_net_pnl_usd=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_PNL_USD") or 0.05),
        min_reward_to_risk=float(os.environ.get("EZRAS_MIN_REWARD_TO_RISK") or 1.10),
        slippage_buffer_bps=float(slip_bps),
    )
    pe = evaluate_profit_enforcement(
        runtime_root=runtime_root,
        trade_id=f"rank_gate_b_{pid}",
        avenue_id="A",
        gate_id="gate_b",
        product_id=pid,
        quote_usd=float(quote_usd_budget),
        spread_bps=float(spread_bps),
        fee_bps_round_trip=float(fee_bps),
        expected_gross_move_bps=float(best.expected_move_bps),
        expected_risk_bps=float(best.expected_risk_bps or max(1.0, float(best.expected_move_bps) / 1.2)),
        config=cfg,
        extra={"surface": "avenue_a_opportunity_ranker", "edge_family": best.edge_type.value, "confidence": best.edge_confidence},
        write_artifact=True,
    )
    ok_pe, why_pe = profit_enforcement_allows_or_reason(pe)

    spread_pen = max(0.0, (spread_bps - 28.0) / 60.0)
    score = float(net_edge_bps) * float(_clamp01(best.edge_confidence)) * (1.0 - 0.25 * _clamp01(spread_pen))

    return (
        RankedOpportunity(
            gate_id="gate_b",
            product_id=pid,
            edge_family=best.edge_type.value,
            confidence=float(best.edge_confidence),
            spread_bps=float(spread_bps),
            fee_bps_round_trip=float(fee_bps),
            slippage_bps_round_trip=float(slip_bps),
            expected_move_bps=float(best.expected_move_bps),
            expected_risk_bps=float(best.expected_risk_bps),
            net_expected_edge_bps=float(net_edge_bps),
            profit_enforcement_allowed=bool(ok_pe),
            profit_enforcement_reason=str(why_pe),
            score=float(score),
            notes={"profit_enforcement": pe, "edge_reason": best.reason, "source_row": chosen_row},
        ),
        diag,
    )


def rank_avenue_a_opportunity(
    *,
    runtime_root: Path,
    client: Any,
    deployable_quote_usd: float,
    anchored_majors_only_for_gate_a: bool,
) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    split = compute_coinbase_gate_capital_split(float(deployable_quote_usd), runtime_root=root)

    ga_budget = float(split.get("gate_a_usd") or 0.0) if split.get("ok") else 0.0
    gb_budget = float(split.get("gate_b_usd") or 0.0) if split.get("ok") else 0.0

    ga, ga_diag = _rank_gate_a(
        runtime_root=root,
        client=client,
        quote_usd_budget=max(0.0, ga_budget),
        anchored_majors_only=bool(anchored_majors_only_for_gate_a),
    )
    gb, gb_diag = _rank_gate_b(
        runtime_root=root,
        client=client,
        quote_usd_budget=max(0.0, gb_budget),
    )

    cands: List[RankedOpportunity] = [x for x in (ga, gb) if x is not None]
    # Hard filter: profit enforcement must allow.
    viable = [c for c in cands if bool(c.profit_enforcement_allowed)]
    chosen: Optional[RankedOpportunity] = max(viable, key=lambda x: float(x.score), default=None) if viable else None

    decision = {
        "truth_version": "opportunity_ranking_truth_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "deployable_quote_usd": float(deployable_quote_usd),
        "capital_split": split,
        "candidates": [
            {
                "gate_id": c.gate_id,
                "product_id": c.product_id,
                "edge_family": c.edge_family,
                "confidence": c.confidence,
                "expected_move_bps": c.expected_move_bps,
                "expected_risk_bps": c.expected_risk_bps,
                "spread_bps": c.spread_bps,
                "fee_bps_round_trip": c.fee_bps_round_trip,
                "slippage_bps_round_trip": c.slippage_bps_round_trip,
                "net_expected_edge_bps": c.net_expected_edge_bps,
                "profit_enforcement_allowed": c.profit_enforcement_allowed,
                "profit_enforcement_reason": c.profit_enforcement_reason,
                "score": c.score,
            }
            for c in cands
        ],
        "diagnostics": {"gate_a": ga_diag, "gate_b": gb_diag},
        "chosen": (
            {
                "gate_id": chosen.gate_id,
                "product_id": chosen.product_id,
                "edge_family": chosen.edge_family,
                "confidence": chosen.confidence,
                "net_expected_edge_bps": chosen.net_expected_edge_bps,
                "score": chosen.score,
                "profit_enforcement_reason": chosen.profit_enforcement_reason,
            }
            if chosen is not None
            else None
        ),
        "no_trade": chosen is None,
        "no_trade_reason": (
            "no_candidate_passed_profit_enforcement_or_edge_detection" if chosen is None else ""
        ),
        "honesty": "Chosen implies only expected net after estimated costs; realized outcomes may differ.",
    }
    _write_truth(root, decision)
    return decision

