"""Active research / edge intelligence — import from submodules (package __init__ stays minimal to avoid cycles)."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "ensure_edge_research_globals":
        from trading_ai.intelligence.edge_research.auto_attach import ensure_edge_research_globals

        return ensure_edge_research_globals
    if name == "ensure_edge_research_for_avenue":
        from trading_ai.intelligence.edge_research.auto_attach import ensure_edge_research_for_avenue

        return ensure_edge_research_for_avenue
    if name == "ensure_edge_research_for_gate":
        from trading_ai.intelligence.edge_research.auto_attach import ensure_edge_research_for_gate

        return ensure_edge_research_for_gate
    if name == "run_discovery":
        from trading_ai.intelligence.edge_research.discovery import run_discovery

        return run_discovery
    if name == "run_daily_edge_research_cycle":
        from trading_ai.intelligence.edge_research.daily_cycle import run_daily_edge_research_cycle

        return run_daily_edge_research_cycle
    if name == "run_edge_research_auto_attach_proof":
        from trading_ai.intelligence.edge_research.proof import run

        return run
    raise AttributeError(name)


__all__ = [
    "ensure_edge_research_globals",
    "ensure_edge_research_for_avenue",
    "ensure_edge_research_for_gate",
    "run_discovery",
    "run_daily_edge_research_cycle",
    "run_edge_research_auto_attach_proof",
]
