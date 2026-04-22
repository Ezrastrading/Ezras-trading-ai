"""
Simulation engine: one tick advances fills, latency, PnL rollups, and durable artifacts.

No venue I/O. Hard-fails if live-trading env flags are enabled.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.memory.store import MemoryStore
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.simulation.fill_lifecycle import advance_simulated_fill_once
from trading_ai.simulation.nonlive import assert_nonlive_for_simulation
from trading_ai.simulation.pnl import build_pnl_rollup
from trading_ai.simulation.regression_drift import compare_recent_vs_baseline, extract_net_points_from_history
from trading_ai.simulation.task_bridge import emit_simulation_tasks, write_sim_tasks_snapshot


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(runtime_root: Optional[Path]) -> Path:
    r = Path(runtime_root or ezras_runtime_root()).resolve()
    return r


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sim_trades(ms: MemoryStore) -> List[Dict[str, Any]]:
    tm = ms.load_json("trade_memory.json")
    xs = tm.get("trades") or []
    out: List[Dict[str, Any]] = []
    for t in xs:
        if isinstance(t, dict) and t.get("simulated_non_live"):
            out.append(t)
    return out


def _weakest_strategy(by_strat: Dict[str, Any]) -> str:
    worst = ""
    worst_net = 1e18
    for k, v in (by_strat or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            n = float(v.get("net_usd") or 0.0)
        except (TypeError, ValueError):
            n = 0.0
        if n < worst_net:
            worst_net = n
            worst = str(k)
    return worst


def run_simulation_tick(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Single autonomous simulation step: fill lifecycle + logs + PnL + comparisons + lessons + tasks.

    Writes under ``<runtime_root>/data/control/``:
    sim_24h_summary, sim_trade_log, sim_fill_log, sim_pnl, sim_lessons, sim_comparisons, sim_tasks
    """
    root = _root(runtime_root)
    assert_nonlive_for_simulation(runtime_root=root)
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    eng_p = ctrl / "simulation" / "engine_state.json"
    eng = _read_json(eng_p)
    seq = int(eng.get("tick_seq") or 0) + 1

    fill_out = advance_simulated_fill_once(runtime_root=root, tick_index=seq)
    fill_log_p = ctrl / "sim_fill_log.json"
    fill_log = _read_json(fill_log_p)
    fills: List[Any] = list(fill_log.get("fills") or [])
    fills.append({"t": _iso(), "seq": seq, "fill": fill_out})
    fills = fills[-4000:]
    _write_json(fill_log_p, {"truth_version": "sim_fill_log_v1", "fills": fills})

    ms = MemoryStore()
    sim_rows = _sim_trades(ms)
    trade_log_p = ctrl / "sim_trade_log.json"
    _write_json(
        trade_log_p,
        {
            "truth_version": "sim_trade_log_v1",
            "generated_at": _iso(),
            "trades": sim_rows[-2000:],
            "count": len(sim_rows),
        },
    )

    prev_pnl = _read_json(ctrl / "sim_pnl.json")
    prev_hist = list((prev_pnl.get("rolling_points") or []))
    rollup = build_pnl_rollup(sim_rows, history_tail=prev_hist)
    net_total = float(rollup.get("net_total_usd") or 0.0)
    rp = list(rollup.get("rolling_points") or [])
    rp.append({"t": _iso(), "net_session_usd": net_total})
    rollup["rolling_points"] = rp[-240:]

    drift = compare_recent_vs_baseline(extract_net_points_from_history(rollup.get("rolling_points") or []))
    rollup["regression_drift"] = drift
    _write_json(ctrl / "sim_pnl.json", rollup)

    weakest = _weakest_strategy(rollup.get("by_strategy") or {})
    comparisons = {
        "truth_version": "sim_comparisons_v1",
        "generated_at": _iso(),
        "weakest_strategy": weakest,
        "by_strategy": rollup.get("by_strategy") or {},
        "honesty": "Derived from simulated trades only.",
    }
    _write_json(ctrl / "sim_comparisons.json", comparisons)

    summary = {
        "truth_version": "sim_24h_summary_v1",
        "generated_at": _iso(),
        "tick_seq": seq,
        "sim_trade_count": len(sim_rows),
        "net_total_usd": rollup.get("net_total_usd"),
        "last_fill_phase": fill_out.get("phase"),
        "latency_note": "see sim_fill_log tail",
        "regression_verdict": drift.get("verdict"),
    }
    _write_json(ctrl / "sim_24h_summary.json", summary)

    lessons_p = ctrl / "sim_lessons.json"
    les = _read_json(lessons_p)
    if not les.get("truth_version"):
        les = {
            "truth_version": "sim_lessons_v1",
            "bootstrap": {
                "note": "initialized_without_failure",
                "started_at": _iso(),
            },
            "lessons": [],
            "cycle_seq": 0,
        }
    cycle_seq = int(les.get("cycle_seq") or 0) + 1
    les["cycle_seq"] = cycle_seq
    les["generated_at"] = _iso()
    items: List[Dict[str, Any]] = list(les.get("lessons") or [])
    term = str(fill_out.get("phase") or "")
    row: Dict[str, Any] = {
        "t": _iso(),
        "seq": seq,
        "trade_cycle": True,
        "fill_phase": term,
        "intent_id": fill_out.get("intent_id"),
        "progression": f"cycle_{cycle_seq}",
    }
    if term in ("filled", "canceled", "rejected"):
        row["terminal"] = term
        row["net_pnl_usd"] = fill_out.get("net_pnl_usd")
    items.append(row)
    les["lessons"] = items[-500:]
    _write_json(lessons_p, les)

    anomaly = None
    if drift.get("emit_corrective_tasks"):
        anomaly = "regression_drift_degrading"
    emitted = emit_simulation_tasks(
        runtime_root=root,
        pnl_doc=rollup,
        comparisons_doc=comparisons,
        regression_doc=drift,
        anomaly_note=anomaly,
    )
    write_sim_tasks_snapshot(runtime_root=root, rows=emitted)

    _write_json(eng_p, {"truth_version": "sim_engine_state_v1", "tick_seq": seq, "updated_at": _iso()})

    return {
        "ok": True,
        "tick_seq": seq,
        "fill": fill_out,
        "pnl_net_total": rollup.get("net_total_usd"),
        "regression": drift,
        "tasks_emitted": len(emitted),
        "artifacts": {
            "sim_24h_summary": str(ctrl / "sim_24h_summary.json"),
            "sim_trade_log": str(ctrl / "sim_trade_log.json"),
            "sim_fill_log": str(ctrl / "sim_fill_log.json"),
            "sim_pnl": str(ctrl / "sim_pnl.json"),
            "sim_lessons": str(ctrl / "sim_lessons.json"),
            "sim_comparisons": str(ctrl / "sim_comparisons.json"),
            "sim_tasks": str(ctrl / "sim_tasks.json"),
        },
    }
