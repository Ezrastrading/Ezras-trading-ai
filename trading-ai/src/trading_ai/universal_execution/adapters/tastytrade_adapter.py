"""
Tastytrade (Avenue C) — options/equities semantics; universal adapter shell with honest gaps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.universal_execution.avenue_adapter import AdapterContext, AvenueAdapterBase, AvenueCapabilityGap


class TastytradeAvenueAdapter(AvenueAdapterBase):
    avenue_id = "C"
    avenue_name = "tastytrade"

    def capability_gaps(self) -> List[AvenueCapabilityGap]:
        return [
            AvenueCapabilityGap(
                code="not_yet_live_universal_round_trip_for_tastytrade",
                detail=(
                    "No Tastytrade fill-confirm + realized PnL round-trip is wired to this universal orchestrator yet. "
                    "Options/equities-specific fields belong under avenue_specific_json when implemented."
                ),
                blocks_live_orders=True,
            ),
        ]

    def scan_candidates(self, ctx: AdapterContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return [], {"note": "tastytrade_scan_not_wired"}

    def select_candidate(
        self, ctx: AdapterContext, candidates: List[Dict[str, Any]]
    ) -> Tuple[Any, Dict[str, Any]]:
        return None, {"reason": "no_candidates"}

    def pretrade_validate(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "tastytrade_universal_adapter_not_wired"}

    def submit_entry(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"reason": "not_wired", "proof_kind": "capability_gap"}

    def confirm_entry_fill(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "not_wired", "truth_source": "none"}

    def compute_exit_plan(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return {"exit_reason": "option_close", "not_wired": True}, {}

    def submit_exit(self, ctx: AdapterContext, exit_plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"reason": "not_wired"}

    def confirm_exit_fill(self, ctx: AdapterContext, exit_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "not_wired", "truth_source": "none"}

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
            "avenue_id": self.avenue_id,
            "instrument_kind": "option",
            "avenue_specific_json": {"venue": "tastytrade", "wiring": "not_yet"},
        }

    def append_local_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"note": "not_wired"}

    def upsert_remote_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"remote_required": False, "note": "not_wired"}

    def refresh_summaries(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"note": "not_wired"}

    def produce_execution_proof(self, ctx: AdapterContext, bundle: Dict[str, Any]) -> Dict[str, Any]:
        return {"scheduler_stable": False, "tastytrade_universal_adapter": "not_wired"}
