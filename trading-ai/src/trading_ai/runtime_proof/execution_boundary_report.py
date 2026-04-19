"""
Execution vs organism-core boundary — serialized contracts for audits (no live trading).

**Organism core** consumes normalized federated trade rows, governance snapshots, and review artifacts.
**Execution** emits venue-specific fills, adapters, and telemetry that must not change governance math.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.review_schema import CANONICAL_TRADE_RECORD_METADATA_KEYS

ORGANISM_CORE_INPUT_KEYS = frozenset(
    {
        "trade_id",
        "avenue",
        "avenue_name",
        "avenue_id",
        "net_pnl_usd",
        "net_pnl",
        "timestamp_open",
        "timestamp_close",
        "truth_provenance",
        "route_bucket",
        "route_label",
        "strategy_class",
        "fees_usd",
        "fees",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "execution_latency_ms",
        "expected_edge_bps",
        "unit",
    }
)


def build_organism_core_inputs_manifest() -> Dict[str, Any]:
    """What the global layer is allowed to depend on (normalized semantics)."""
    return {
        "schema": "organism_core_inputs_v1",
        "federated_trade_optional_metadata_keys": sorted(CANONICAL_TRADE_RECORD_METADATA_KEYS),
        "federated_trade_core_keys": sorted(ORGANISM_CORE_INPUT_KEYS),
        "governance_inputs": ["joint_review_latest.json live_mode_recommendation", "review_integrity_state"],
        "non_inputs_for_routing": [
            "strategy_class does not select organism route buckets (use route_bucket / route_label only)",
            "latency and edge are observability fields in packets, not gate inputs",
        ],
    }


def write_boundary_artifacts(
    runtime_root: Path,
    *,
    extra_execution_notes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Path]:
    """
    Write ``organism_core_inputs.json``, ``execution_boundary_report.json``, ``avenue_specific_behaviors.json``.
    """
    runtime_root = runtime_root.resolve()
    out = runtime_root / "boundary_proof"
    out.mkdir(parents=True, exist_ok=True)

    core = build_organism_core_inputs_manifest()
    (out / "organism_core_inputs.json").write_text(json.dumps(core, indent=2), encoding="utf-8")

    boundary = {
        "schema": "execution_boundary_report_v1",
        "EZRAS_RUNTIME_ROOT": os.environ.get("EZRAS_RUNTIME_ROOT"),
        "TRADE_DATABANK_MEMORY_ROOT": os.environ.get("TRADE_DATABANK_MEMORY_ROOT"),
        "notes": extra_execution_notes or {},
        "boundary_rule": "Venue adapters write databank/trade_memory; organism reads federated merge only.",
    }
    (out / "execution_boundary_report.json").write_text(json.dumps(boundary, indent=2), encoding="utf-8")

    avenue_behaviors = {
        "schema": "avenue_specific_behaviors_v1",
        "coinbase": ["NTE engine", "coinbase_close_adapter", "process_closed_trade"],
        "kalshi": ["KalshiClient outlets", "kalshi_execution_mirror (visibility)"],
        "manifold": ["play_money unit labeling"],
    }
    (out / "avenue_specific_behaviors.json").write_text(json.dumps(avenue_behaviors, indent=2), encoding="utf-8")

    return {
        "organism_core_inputs": out / "organism_core_inputs.json",
        "execution_boundary_report": out / "execution_boundary_report.json",
        "avenue_specific_behaviors": out / "avenue_specific_behaviors.json",
    }
