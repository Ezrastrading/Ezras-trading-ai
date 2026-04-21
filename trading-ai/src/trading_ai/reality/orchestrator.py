"""Single entrypoint: record a closed trade through all reality + trade-log layers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

from trading_ai.reality.discipline_engine import DisciplineEngine, ViolationKind
from trading_ai.reality.edge_truth import EdgeTruthEngine
from trading_ai.reality.execution_truth import (
    ExecutionTruthRecord,
    append_execution_truth_record,
    compute_execution_truth,
)
from trading_ai.reality.sample_validation import validate_sample
from trading_ai.reality.trade_logger import append_trade_record
from trading_ai.reality.verdict import build_reality_verdict


def _actual_edge_bps(
    entry_price: float,
    exit_price: float,
) -> float:
    e = abs(float(entry_price))
    if e < 1e-12:
        return 0.0
    return 1e4 * (float(exit_price) - float(entry_price)) / e


def record_closed_trade(
    *,
    timestamp: str,
    venue: str,
    edge_id: str,
    product: str,
    expected_entry_price: float,
    actual_entry_price: float,
    expected_exit_price: float,
    actual_exit_price: float,
    base_size: float,
    fees_paid: float,
    regime: str = "",
    latency_ms: float = 0.0,
    expected_edge_bps: float = 0.0,
    violations: Optional[Sequence[str]] = None,
    edge_engine: Optional[EdgeTruthEngine] = None,
    discipline_engine: Optional[DisciplineEngine] = None,
) -> Dict[str, Any]:
    """
    Measurement-only pipeline: execution truth JSONL, edge truth summary, discipline log,
    trade raw JSONL + summaries, aggregated verdict row.
    """
    ex = compute_execution_truth(
        expected_entry_price=expected_entry_price,
        actual_entry_price=actual_entry_price,
        expected_exit_price=expected_exit_price,
        actual_exit_price=actual_exit_price,
        base_size=base_size,
        fees_paid=fees_paid,
        trade_id=str(product or "") or None,
    )
    append_execution_truth_record(ex)

    ee = edge_engine or EdgeTruthEngine()
    edge_summary = ee.record_trade(edge_id, gross_pnl=ex.gross_pnl, net_pnl=ex.net_pnl)

    de = discipline_engine or DisciplineEngine()
    viol = [str(v) for v in (violations or []) if str(v).strip()]
    disc = de.evaluate(viol)
    nets_for_edge = ee.net_pnls(edge_id)
    sample = validate_sample(nets_for_edge)

    verdict_row = build_reality_verdict(
        edge_id=edge_id,
        venue=venue,
        edge_summary=ee.summary_for_edge(edge_id),
        net_pnls_for_edge=nets_for_edge,
        execution_flag=ex.flag,
        discipline_score=disc.discipline_score,
        sample_result=sample.to_dict(),
    )

    raw_record = {
        "timestamp": timestamp,
        "venue": venue,
        "edge_id": edge_id,
        "product": product,
        "entry_price": actual_entry_price,
        "exit_price": actual_exit_price,
        "base_size": base_size,
        "expected_edge_bps": float(expected_edge_bps),
        "actual_edge_bps": _actual_edge_bps(actual_entry_price, actual_exit_price),
        "gross_pnl": ex.gross_pnl,
        "fees": float(fees_paid),
        "net_pnl": ex.net_pnl,
        "slippage_entry_bps": ex.slippage_entry_bps,
        "slippage_exit_bps": ex.slippage_exit_bps,
        "latency_ms": float(latency_ms),
        "regime": regime,
        "discipline_score": disc.discipline_score,
        "execution_flag": ex.flag,
        "edge_truth": edge_summary.get("edge_status", ""),
        "confidence_level": sample.confidence_level,
    }
    log_info = append_trade_record(raw_record)
    try:
        from trading_ai.reporting.trader_visibility import run_trader_visibility_after_close

        vis = dict(raw_record)
        vis["timestamp_close"] = vis.get("timestamp")
        vis["timestamp_open"] = vis.get("timestamp")
        vis["avenue_name"] = str(venue)
        vis["asset"] = str(product)
        vis["hold_seconds"] = 0.0
        run_trader_visibility_after_close(vis)
    except Exception as exc:
        logger.warning("trader_visibility (reality orchestrator): %s", exc)
    return {
        "execution": ex.to_dict(),
        "edge_summary": edge_summary,
        "discipline": disc.to_dict(),
        "sample_validation": sample.to_dict(),
        "verdict": verdict_row,
        "violations": viol,
        "trade_log": log_info,
    }


__all__ = [
    "record_closed_trade",
    "ViolationKind",
    "EdgeTruthEngine",
    "DisciplineEngine",
    "ExecutionTruthRecord",
]
