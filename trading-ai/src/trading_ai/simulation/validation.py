"""Strict post-run validation for long simulation batches (non-wall-clock accelerated)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from trading_ai.global_layer.task_registry import load_all_tasks
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_sim_24h_validation(
    *,
    runtime_root: Optional[Path] = None,
    min_simulated_trades: int = 100,
    min_supervisor_cycles: int = 8,
    ticks_executed: int = 0,
) -> Dict[str, Any]:
    """
    Confirm the system evolved (trades, lessons, tasks) rather than a static no-op loop.
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    sim_log = ctrl / "sim_trade_log.json"
    les_p = ctrl / "sim_lessons.json"
    trade_count = 0
    if sim_log.is_file():
        try:
            doc = json.loads(sim_log.read_text(encoding="utf-8"))
            trade_count = int(doc.get("count") or len(doc.get("trades") or []))
        except (OSError, json.JSONDecodeError, TypeError):
            trade_count = 0

    cycle_seq = 0
    lesson_n = 0
    if les_p.is_file():
        try:
            les = json.loads(les_p.read_text(encoding="utf-8"))
            cycle_seq = int(les.get("cycle_seq") or 0)
            lesson_n = len(list(les.get("lessons") or []))
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    gov_tasks = load_all_tasks()
    types: Set[str] = set()
    for t in gov_tasks[-500:]:
        if isinstance(t, dict) and t.get("task_type"):
            types.add(str(t.get("task_type")))

    ctrl_tasks = ctrl / "tasks.jsonl"
    if ctrl_tasks.is_file():
        for ln in ctrl_tasks.read_text(encoding="utf-8").splitlines()[-500:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
                if row.get("task_type"):
                    types.add(str(row["task_type"]))
            except json.JSONDecodeError:
                continue

    present_required = {
        "comparisons::avenue": any(tt == "comparisons::avenue" for tt in types),
        "risk_reduction": any(tt == "risk_reduction" or "risk_reduction" in tt for tt in types),
        "regression::investigate": any(tt == "regression::investigate" for tt in types),
        "mission_goals": any(str(tt).startswith("mission_goals::") for tt in types),
    }

    evolved = trade_count >= int(min_simulated_trades) and int(ticks_executed) >= int(min_supervisor_cycles)
    routing_core = present_required["comparisons::avenue"] and (
        present_required["mission_goals"] or present_required["risk_reduction"]
    )
    ok = evolved and lesson_n >= 3 and len(types) >= 4 and routing_core

    doc: Dict[str, Any] = {
        "truth_version": "sim_24h_validation_v1",
        "generated_at": _iso(),
        "ok": bool(ok),
        "runtime_root": str(root),
        "min_simulated_trades": int(min_simulated_trades),
        "sim_trade_count": trade_count,
        "ticks_executed": int(ticks_executed),
        "sim_lesson_cycle_seq": cycle_seq,
        "sim_lesson_rows": lesson_n,
        "distinct_task_types_sampled": sorted(types)[:80],
        "task_type_probes": present_required,
        "honesty": "Validation uses sim_trade_log + governance tasks + control task mirror; not venue proof.",
    }
    if not ok:
        doc["failure_reasons"] = []
        if trade_count < min_simulated_trades:
            doc["failure_reasons"].append("insufficient_sim_trades")
        if int(ticks_executed) < min_supervisor_cycles:
            doc["failure_reasons"].append("insufficient_supervisor_ticks")
        if lesson_n <= 0:
            doc["failure_reasons"].append("no_sim_lessons")
        if len(types) < 4:
            doc["failure_reasons"].append("insufficient_task_type_diversity")

    p = ctrl / "sim_24h_validation.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return doc
