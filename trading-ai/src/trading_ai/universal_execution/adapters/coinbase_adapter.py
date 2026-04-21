"""
Coinbase (Avenue A) — universal adapter shell.

Live execution and Gate A / Gate B proof remain in NTE ``coinbase_engine``, deployment micro-validation,
and Gate B artifacts. This adapter does not duplicate that stack; it reports an explicit wiring gap for
:class:`execute_round_trip_with_truth` until delegated without weakening existing honesty semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.universal_execution.avenue_adapter import AdapterContext, AvenueAdapterBase, AvenueCapabilityGap


class CoinbaseAvenueAdapter(AvenueAdapterBase):
    avenue_id = "A"
    avenue_name = "coinbase"

    def capability_gaps(self) -> List[AvenueCapabilityGap]:
        return [
            AvenueCapabilityGap(
                code="not_yet_live_universal_orchestrator_wired_to_nte_coinbase_engine",
                detail=(
                    "Universal round-trip orchestrator is not yet delegated to trading_ai.nte.execution.coinbase_engine "
                    "or the Gate B production path. Use `python -m trading_ai.deployment gate-b-live-micro` for Gate B "
                    "live proof and `gate-b-tick` for production ticks — those preserve existing execution truth."
                ),
                blocks_live_orders=True,
            ),
        ]

    def scan_candidates(self, ctx: AdapterContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return [], {"note": "scan_delegates_to_gate_b_scanner_when_wired", "gate_id": ctx.gate_id}

    def select_candidate(
        self, ctx: AdapterContext, candidates: List[Dict[str, Any]]
    ) -> Tuple[Any, Dict[str, Any]]:
        return None, {"reason": "no_candidates_without_scan_wiring"}

    def pretrade_validate(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "universal_adapter_not_wired", "adaptive": {}, "duplicate_guard": {}}

    def submit_entry(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"reason": "not_wired", "proof_source": "coinbase_avenue_adapter", "proof_kind": "capability_gap"}

    def confirm_entry_fill(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "not_wired", "truth_source": "none", "proof_kind": "none"}

    def compute_exit_plan(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return {"exit_reason": "not_wired"}, {"note": "capability_gap"}

    def submit_exit(self, ctx: AdapterContext, exit_plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"reason": "not_wired", "proof_source": "coinbase_avenue_adapter", "proof_kind": "capability_gap"}

    def confirm_exit_fill(self, ctx: AdapterContext, exit_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "not_wired", "truth_source": "none", "proof_kind": "none"}

    def compute_realized_pnl(
        self, ctx: AdapterContext, entry_meta: Dict[str, Any], exit_meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return {"complete": False}, {"note": "not_wired"}

    def build_trade_record(
        self,
        ctx: AdapterContext,
        *,
        entry_meta: Dict[str, Any],
        exit_meta: Dict[str, Any],
        pnl_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "trade_id": "",
            "avenue_id": self.avenue_id,
            "avenue_name": self.avenue_name,
            "gate_id": ctx.gate_id,
            "truth_version": "normalized_trade_record_v1",
            "avenue_specific_json": {"adapter": "CoinbaseAvenueAdapter", "wiring": "not_yet"},
        }

    def append_local_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"note": "not_wired"}

    def upsert_remote_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"remote_required": False, "note": "not_wired_remote_skipped"}

    def refresh_summaries(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"note": "not_wired"}

    def produce_execution_proof(self, ctx: AdapterContext, bundle: Dict[str, Any]) -> Dict[str, Any]:
        return {"scheduler_stable": False, "note": "coinbase_universal_adapter_not_wired"}
