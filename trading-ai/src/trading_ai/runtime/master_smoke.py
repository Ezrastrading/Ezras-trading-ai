"""Master smoke: supervisor + simulation + tasks + live lock (durable ``master_smoke.json``)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.simulation.nonlive import assert_nonlive_for_simulation


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def run_master_smoke(*, runtime_root: Path, cycles: int = 14) -> Dict[str, Any]:
    from trading_ai.control.full_autonomy_mode import write_full_autonomy_active_live_artifacts
    from trading_ai.global_layer.task_registry import load_all_tasks
    from trading_ai.runtime.now_live_proof import run_authoritative_live_guard_proof
    from trading_ai.runtime.operating_system import enforce_non_live_env_defaults, run_role_supervisor_once
    from trading_ai.runtime.regression_drift import analyze_and_write_regression_drift
    from trading_ai.simulation.engine import run_simulation_tick

    enforce_non_live_env_defaults()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root.resolve())
    assert_nonlive_for_simulation(runtime_root=runtime_root)
    write_full_autonomy_active_live_artifacts(runtime_root=runtime_root, reason="master_smoke", apply_env=False)
    ran_ops: List[str] = []
    ran_rs: List[str] = []
    for _ in range(max(3, int(cycles))):
        run_simulation_tick(runtime_root=runtime_root)
        o = run_role_supervisor_once(role="ops", runtime_root=runtime_root, skip_models=True, force_all_due=True)
        r = run_role_supervisor_once(role="research", runtime_root=runtime_root, skip_models=True, force_all_due=True)
        ran_ops.extend(list(o.get("ran") or []))
        ran_rs.extend(list(r.get("ran") or []))

    analyze_and_write_regression_drift(runtime_root=runtime_root)
    lg_ok, lg = run_authoritative_live_guard_proof(runtime_root=runtime_root)

    ctrl = runtime_root / "data" / "control"
    artifacts = {
        "loop_status_ops": (ctrl / "operating_system" / "loop_status_ops.json").is_file(),
        "loop_status_research": (ctrl / "operating_system" / "loop_status_research.json").is_file(),
        "regression_drift": (ctrl / "regression_drift.json").is_file(),
        "sim_trade_log": (ctrl / "sim_trade_log.json").is_file(),
        "sim_fill_log": (ctrl / "sim_fill_log.json").is_file(),
        "sim_pnl": (ctrl / "sim_pnl.json").is_file(),
        "sim_lessons": (ctrl / "sim_lessons.json").is_file(),
        "sim_tasks": (ctrl / "sim_tasks.json").is_file(),
        "full_autonomy_mode": (ctrl / "full_autonomy_mode.json").is_file(),
        "full_autonomy_live_status": (ctrl / "full_autonomy_live_status.json").is_file(),
        "lessons_control": (ctrl / "lessons.json").is_file(),
        "review_cycle": (ctrl / "review_cycle.json").is_file(),
        "ceo_daily_review_control": (ctrl / "ceo_daily_review.json").is_file(),
        "mission_goals_plan": (ctrl / "mission_goals_operating_plan.json").is_file(),
        "pnl_review": (ctrl / "pnl_review.json").is_file(),
        "performance_comparisons": (ctrl / "performance_comparisons.json").is_file(),
        "bot_inboxes": bool(list((ctrl / "bot_inboxes").glob("*.json"))),
        "tasks_jsonl_mirror": (ctrl / "tasks.jsonl").is_file(),
    }
    tasks = load_all_tasks()
    types: Set[str] = set()
    for t in tasks[-800:]:
        if isinstance(t, dict) and t.get("task_type"):
            types.add(str(t["task_type"]))
    tpath = ctrl / "tasks.jsonl"
    if tpath.is_file():
        for ln in tpath.read_text(encoding="utf-8").splitlines()[-800:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
                if row.get("task_type"):
                    types.add(str(row["task_type"]))
            except json.JSONDecodeError:
                continue

    ok = all(artifacts.values()) and bool(lg_ok)
    out: Dict[str, Any] = {
        "truth_version": "master_smoke_v2",
        "generated_at": _iso(),
        "ok": ok,
        "runtime_root": str(runtime_root),
        "artifacts": artifacts,
        "ops_loops_touched": sorted(set(ran_ops)),
        "research_loops_touched": sorted(set(ran_rs)),
        "task_types_observed": sorted(types),
        "live_guard_ok": bool(lg_ok),
    }
    p = ctrl / "master_smoke.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return out


def default_runtime_root() -> Path:
    return Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
