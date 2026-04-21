"""Dispatchers for ``python -m trading_ai.deployment avenue-*`` — honest per-avenue behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.runtime_paths import ezras_runtime_root


def run_avenue_status(avenue: str) -> Dict[str, Any]:
    av = (avenue or "").strip().upper()
    from trading_ai.universal_execution.runtime_avenue_remaining_gaps import build_universal_remaining_gaps
    from trading_ai.universal_execution.universal_live_switch_truth import build_universal_live_switch_truth

    root = Path(ezras_runtime_root()).resolve()
    switch_truth = build_universal_live_switch_truth(runtime_root=root)
    gaps = build_universal_remaining_gaps(runtime_root=root)
    view = switch_truth.get("avenues", {}).get(av) or {}
    return {
        "avenue": av,
        "universal_live_switch_truth": switch_truth,
        "avenue_view": view,
        "universal_remaining_gaps": gaps,
        "honesty": "Status is artifact-driven. Avenue B/C universal live-switch remains false until wired.",
    }


def run_avenue_tick(avenue: str, *, persist_gate_b_adaptive: bool = False) -> Dict[str, Any]:
    av = (avenue or "").strip().upper()
    root = Path(ezras_runtime_root()).resolve()
    from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change

    if av == "A":
        from trading_ai.deployment.gate_b_production_tick import run_gate_b_production_tick

        tick = run_gate_b_production_tick(persist_gate_b_adaptive_state=bool(persist_gate_b_adaptive))
        r = tick.get("runtime_truth_refresh_after_tick") or {}
        return {
            "avenue": av,
            "note": (
                "Avenue A tick uses Gate B production tick (scan + adaptive + engine on last rows; no orders). "
                "Gate A / NTE-specific live loops use separate entrypoints — not duplicated here. "
                "Runtime artifact refresh runs after the tick writes gate_b_last_production_tick.json."
            ),
            "refresh_runtime_artifacts": r.get("refresh_complete_and_trustworthy"),
            "gate_b_production_tick": tick,
        }
    if av == "B":
        refresh = refresh_runtime_truth_after_material_change(
            reason="avenue_tick_B",
            runtime_root=root,
            force=False,
            include_advisory=True,
        )
        return {
            "avenue": av,
            "tick_ok": False,
            "honesty": "No Kalshi production tick is wired to this universal avenue-tick dispatch yet.",
            "refresh_runtime_artifacts": refresh.get("refresh_complete_and_trustworthy"),
        }
    if av == "C":
        refresh = refresh_runtime_truth_after_material_change(
            reason="avenue_tick_C",
            runtime_root=root,
            force=False,
            include_advisory=True,
        )
        return {
            "avenue": av,
            "tick_ok": False,
            "honesty": "No Tastytrade production tick is wired to this universal avenue-tick dispatch yet.",
            "refresh_runtime_artifacts": refresh.get("refresh_complete_and_trustworthy"),
        }
    return {"error": "unknown_avenue", "expected": ["A", "B", "C"]}


def run_write_remaining_gaps() -> Dict[str, Any]:
    from trading_ai.universal_execution.runtime_avenue_remaining_gaps import write_universal_remaining_gaps_artifact

    return write_universal_remaining_gaps_artifact()


def run_write_live_switch_truth() -> Dict[str, Any]:
    from trading_ai.universal_execution.universal_live_switch_truth import write_universal_live_switch_truth_artifact

    return write_universal_live_switch_truth_artifact()
