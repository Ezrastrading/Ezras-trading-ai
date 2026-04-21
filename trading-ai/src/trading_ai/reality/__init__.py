"""Reality validation layer — execution quality, edge truth, discipline, sample stats (no orders)."""

from trading_ai.reality.discipline_engine import DisciplineEngine, ViolationKind
from trading_ai.reality.edge_truth import EdgeTruthEngine
from trading_ai.reality.execution_truth import (
    ExecutionTruthRecord,
    append_execution_truth_from_databank_trade,
    append_execution_truth_record,
    compute_execution_truth,
    compute_execution_truth_from_merged_trade,
)
from trading_ai.reality.orchestrator import record_closed_trade
from trading_ai.reality.sample_validation import SampleValidationResult, validate_sample
from trading_ai.reality.trade_logger import (
    append_trade_record,
    milestone_verdict,
    trades_raw_path,
)
from trading_ai.reality.verdict import build_reality_verdict, verdict_from_engines

__all__ = [
    "DisciplineEngine",
    "EdgeTruthEngine",
    "ExecutionTruthRecord",
    "SampleValidationResult",
    "ViolationKind",
    "append_execution_truth_from_databank_trade",
    "append_execution_truth_record",
    "append_trade_record",
    "build_reality_verdict",
    "compute_execution_truth",
    "compute_execution_truth_from_merged_trade",
    "milestone_verdict",
    "record_closed_trade",
    "trades_raw_path",
    "validate_sample",
    "verdict_from_engines",
]
