"""
Universal normalized trade row shape — avenue-specific payload only under ``avenue_specific_json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedTradeRecord:
    trade_id: str = ""
    created_at: str = ""
    avenue_id: str = ""  # A | B | C | ...
    avenue_name: str = ""
    gate_id: str = ""  # gate_a | gate_b | ...
    trade_type: str = "core"  # core | asymmetric
    capital_bucket_id: str = "core"
    strategy_id: str = ""
    execution_profile: str = ""
    instrument_kind: str = ""  # spot | option | prediction_contract | ...
    product_id: str = ""
    symbol: str = ""
    side_entry: str = ""
    side_exit: str = ""
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    entry_fill_confirmed: bool = False
    exit_fill_confirmed: bool = False
    entry_truth_source: str = ""
    exit_truth_source: str = ""
    quantity_filled: Optional[float] = None
    contracts_filled: Optional[float] = None
    quote_spent: Optional[float] = None
    proceeds_received: Optional[float] = None
    fees_paid: Optional[float] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None
    return_bps: Optional[float] = None
    exit_reason: str = ""
    hold_seconds: Optional[float] = None
    adaptive_scope: str = ""
    governance_allowed: Optional[bool] = None
    duplicate_guard_mode: str = ""
    validation_scope_key: str = ""
    local_write_ok: bool = False
    remote_write_ok: bool = False
    review_update_ok: bool = False
    ready_for_next_cycle: bool = False
    partial_failure_codes: List[str] = field(default_factory=list)
    proof_kind: str = ""
    proof_axis: str = ""
    truth_version: str = "normalized_trade_record_v1"
    avenue_specific_json: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
