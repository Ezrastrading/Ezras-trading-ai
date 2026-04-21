"""
Avenue-agnostic execution truth contract, lifecycle orchestration, and adapters.

Live venue wiring remains in NTE / Shark / venue modules; this package defines the contract
and honest capability boundaries — it does not replace coinbase_engine with a duplicate.
"""

from trading_ai.universal_execution.execution_truth_contract import (
    ExecutionTruthContract,
    ExecutionTruthStage,
    StageStatus,
)
from trading_ai.universal_execution.rebuy_policy import (
    TerminalHonestState,
    can_open_next_trade_after,
)
from trading_ai.universal_execution.normalized_trade_record import NormalizedTradeRecord
from trading_ai.universal_execution.universal_trade_cycle import (
    execute_round_trip_with_truth,
    run_universal_trade_cycle,
)
from trading_ai.universal_execution.universal_execution_proof import (
    build_universal_execution_proof_payload,
    write_universal_execution_validation,
)
from trading_ai.universal_execution.universal_execution_loop_proof import (
    ExecutionLifecycleState,
    build_universal_execution_loop_proof_payload,
    write_loop_proof_from_trade_result,
)
from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change

__all__ = [
    "ExecutionTruthContract",
    "ExecutionTruthStage",
    "StageStatus",
    "TerminalHonestState",
    "can_open_next_trade_after",
    "NormalizedTradeRecord",
    "execute_round_trip_with_truth",
    "run_universal_trade_cycle",
    "build_universal_execution_proof_payload",
    "write_universal_execution_validation",
    "ExecutionLifecycleState",
    "build_universal_execution_loop_proof_payload",
    "write_loop_proof_from_trade_result",
    "refresh_runtime_truth_after_material_change",
]
