"""
Avenue adapter protocol — map venue semantics to the universal truth contract.

No live order placement is required here; production paths remain in NTE / Shark / venue modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple


@dataclass
class AvenueCapabilityGap:
    """Explicit honest gap — never masquerade as success."""

    code: str
    detail: str
    blocks_live_orders: bool = True


@dataclass
class AdapterContext:
    avenue_id: str
    gate_id: str = ""
    strategy_id: str = ""
    execution_profile: str = ""
    route: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class AvenueAdapterBase(ABC):
    avenue_id: str = ""
    avenue_name: str = ""

    def capability_gaps(self) -> List[AvenueCapabilityGap]:
        """Declare what is not yet wired for universal round-trip orchestration."""
        return []

    @abstractmethod
    def scan_candidates(self, ctx: AdapterContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        ...

    @abstractmethod
    def select_candidate(
        self, ctx: AdapterContext, candidates: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        ...

    @abstractmethod
    def pretrade_validate(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def submit_entry(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def confirm_entry_fill(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def compute_exit_plan(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ...

    @abstractmethod
    def submit_exit(self, ctx: AdapterContext, exit_plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def confirm_exit_fill(self, ctx: AdapterContext, exit_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def compute_realized_pnl(
        self, ctx: AdapterContext, entry_meta: Dict[str, Any], exit_meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ...

    @abstractmethod
    def build_trade_record(
        self,
        ctx: AdapterContext,
        *,
        entry_meta: Dict[str, Any],
        exit_meta: Dict[str, Any],
        pnl_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def append_local_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def upsert_remote_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def refresh_summaries(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        ...

    @abstractmethod
    def produce_execution_proof(self, ctx: AdapterContext, bundle: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def governance_log(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """Override when governance writes are required for the avenue."""
        return True, {"governance_required": False}


class SupportsUniversalCycle(Protocol):
    """Structural typing hook for tests and optional duck-typed adapters."""

    avenue_id: str
