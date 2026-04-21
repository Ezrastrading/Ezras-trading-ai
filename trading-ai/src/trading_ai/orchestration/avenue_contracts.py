"""Avenue capability / proof / adapter contracts (registry-backed, no Coinbase-only core)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions


class AvenueExecutionAdapter(Protocol):
    """Venue-specific order submission; implementations live per avenue package."""

    avenue_id: str

    def submit_entry(self, intent: Dict[str, Any]) -> Dict[str, Any]: ...

    def submit_exit(self, intent: Dict[str, Any]) -> Dict[str, Any]: ...


class FillConfirmationAdapter(Protocol):
    def poll_buy_fill(self, order_ref: Dict[str, Any]) -> Dict[str, Any]: ...

    def poll_sell_fill(self, order_ref: Dict[str, Any]) -> Dict[str, Any]: ...


@dataclass
class AvenueCapabilityContract:
    avenue_id: str
    can_scan: bool
    can_tick: bool
    can_paper_trade: bool
    can_live_trade: bool
    can_run_autonomously: bool
    live_proof_path: str
    readiness_artifact_path: str
    adaptive_scope_key: str
    governance_operation_name: str
    capital_truth_source: str
    duplicate_guard_scope: str
    execution_confirmation_mode: str  # e.g. "poll_rest", "websocket", "none_staged"


def _default_contracts() -> Dict[str, AvenueCapabilityContract]:
    """Honest defaults: A may be wired; B/C staged unless independent proof files exist."""
    return {
        "A": AvenueCapabilityContract(
            avenue_id="A",
            can_scan=True,
            can_tick=True,
            can_paper_trade=True,
            can_live_trade=True,
            can_run_autonomously=False,
            live_proof_path="data/control/go_no_go_decision.json",
            readiness_artifact_path="data/control/system_execution_lock.json",
            adaptive_scope_key="avenue.A.coinbase_nte",
            governance_operation_name="new_entry",
            capital_truth_source="coinbase_quote_balances_and_deployable_capital_report",
            duplicate_guard_scope="gate_a:product_action_gate",
            execution_confirmation_mode="poll_rest",
        ),
        "B": AvenueCapabilityContract(
            avenue_id="B",
            can_scan=True,
            can_tick=True,
            can_paper_trade=True,
            can_live_trade=False,
            can_run_autonomously=False,
            live_proof_path="data/control/gate_b_validation.json",
            readiness_artifact_path="data/control/system_execution_lock.json",
            adaptive_scope_key="avenue.B.kalshi",
            governance_operation_name="new_entry",
            capital_truth_source="kalshi_balance_and_position_limits",
            duplicate_guard_scope="gate_b:product_action_gate",
            execution_confirmation_mode="poll_rest",
        ),
        "C": AvenueCapabilityContract(
            avenue_id="C",
            can_scan=False,
            can_tick=False,
            can_paper_trade=False,
            can_live_trade=False,
            can_run_autonomously=False,
            live_proof_path="data/control/avenue_C_independent_live_proof.json",
            readiness_artifact_path="data/control/system_execution_lock.json",
            adaptive_scope_key="avenue.C.reserved",
            governance_operation_name="new_entry",
            capital_truth_source="not_wired",
            duplicate_guard_scope="avenue_C_none",
            execution_confirmation_mode="none_staged",
        ),
    }


def merged_capabilities(*, runtime_root: Optional[Path] = None) -> Dict[str, AvenueCapabilityContract]:
    defs = {str(a["avenue_id"]): a for a in merged_avenue_definitions(runtime_root=runtime_root)}
    base = _default_contracts()
    out = dict(base)
    for aid, row in defs.items():
        if aid not in out:
            out[aid] = AvenueCapabilityContract(
                avenue_id=aid,
                can_scan=row.get("wiring_status") == "wired",
                can_tick=row.get("wiring_status") == "wired",
                can_paper_trade=False,
                can_live_trade=False,
                can_run_autonomously=False,
                live_proof_path=f"data/control/avenue_{aid}_live_proof.json",
                readiness_artifact_path="data/control/system_execution_lock.json",
                adaptive_scope_key=f"avenue.{aid}",
                governance_operation_name="new_entry",
                capital_truth_source="unknown",
                duplicate_guard_scope=f"avenue_{aid}",
                execution_confirmation_mode="none_staged",
            )
    return out
