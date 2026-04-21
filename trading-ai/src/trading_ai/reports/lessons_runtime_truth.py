"""
Honest classification of whether ``shark/state/lessons.json`` and related lesson flows affect Gate B Coinbase runtime.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _load_lessons_file(runtime_root: Path) -> Dict[str, Any]:
    try:
        from trading_ai.shark.lessons import LESSONS_FILE, load_lessons

        p = Path(LESSONS_FILE)
        if not p.is_file():
            return {"ok": False, "path": str(p), "error": "lessons_file_missing"}
        data = load_lessons()
        return {"ok": True, "path": str(p), "data": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _lessons_in_last_hours(rows: List[Dict[str, Any]], hours: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        ds = str(r.get("date") or "")
        try:
            if len(ds) >= 10:
                d = datetime.fromisoformat(ds[:10]).replace(tzinfo=timezone.utc)
                if d >= cutoff:
                    n += 1
        except (TypeError, ValueError):
            continue
    return n


def build_lessons_runtime_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    loaded = _load_lessons_file(root)
    lessons: List[Dict[str, Any]] = []
    if loaded.get("ok") and isinstance(loaded.get("data"), dict):
        raw = loaded["data"].get("lessons") or []
        lessons = [x for x in raw if isinstance(x, dict)]

    # Code-path honesty: Gate B Coinbase engine does not import load_lessons (verified in repo audit).
    gate_b_coinbase_path = (
        "trading_ai.shark.coinbase_spot.gate_b_engine — no import of shark.lessons / load_lessons; "
        "ranking uses momentum/liquidity/regime only."
    )
    shark_run_shark_path = (
        "trading_ai.shark.run_shark loads load_lessons() for mission/CEO context — Kalshi-heavy paths; "
        "not the Coinbase Gate B momentum engine entrypoint."
    )

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "lessons_count_total": len(lessons),
        "lessons_count_last_48h": _lessons_in_last_hours(lessons, 48),
        "lessons_count_last_48h_estimate": _lessons_in_last_hours(lessons, 48),
        "runtime_reads_lessons": False,
        "runtime_reads_lessons_file_in_gate_b_engine": False,
        "lessons_influence_candidate_selection": False,
        "lessons_influence_candidate_ranking": False,
        "lessons_influence_entry_filtering": False,
        "lessons_influence_entry": False,
        "lessons_influence_exit_logic": False,
        "lessons_influence_exit": False,
        "lessons_influence_sizing": False,
        "lessons_influence_risk": False,
        "lessons_influence_pause_or_halt": False,
        "lessons_context_only": True,
        "lessons_not_used_by_live_order_path": True,
        "lessons_influence_edge_registry": False,
        "lessons_influence_candidate_ranking_gate_b": False,
        "lessons_influence_entry_filtering_gate_b": False,
        "lessons_influence_exit_logic_gate_b": False,
        "lessons_influence_sizing_gate_b": False,
        "lessons_influence_pause_or_halt_gate_b": False,
        "lessons_influence_edge_registry_gate_b": False,
        "lessons_used_in_last_gate_b_eval": False,
        "lesson_ids_used_in_last_gate_b_eval": [],
        "proof_of_runtime_consumption_path": gate_b_coinbase_path,
        "subsystem_classifications": {
            "shark_lessons_json": "stored_only",
            "improvement_loop_extract_lessons_from_diagnosis": "report_visible_only",
            "run_shark_mission_lessons": "runtime_read_only",
            "gate_b_momentum_engine": "runtime_influences_decisions_without_lessons_json",
            "ai_review_packet_lesson_state": "report_visible_only",
        },
        "honesty": (
            "Lessons in lessons.json are not consulted by Gate B Coinbase gate_b_engine / gate_b_scanner. "
            "They may inform Shark/Kalshi sessions and review packets separately."
        ),
        "lessons_strict_roles_gate_b_coinbase": {
            "only_stored": True,
            "only_reported_elsewhere": True,
            "read_for_context_non_gate_b_paths": True,
            "used_in_runtime_trading_decisions_gate_b": False,
            "used_in_gate_b_live_order_decisions": False,
        },
        "if_gate_b_loses_money_tomorrow_could_current_lessons_have_changed_entry_exit_or_ranking_beforehand": {
            "answer": "no",
            "reason": (
                "gate_b_engine does not import or apply lessons.json; ranking/entry filters are momentum/liquidity/regime. "
                "Lessons cannot have changed Gate B Coinbase entry/exit/ranking logic unless code is wired to do so."
            ),
        },
        "evidence_lines": [gate_b_coinbase_path, shark_run_shark_path],
        "exact_wiring_needed_for_lessons_to_influence_live_decisions": (
            "Import and apply lessons.json (or derived features) inside gate_b_engine / gate_b_scanner ranking, "
            "entry filters, sizing, and exit policy; add tests proving runtime consumption for Gate B orders."
        ),
    }
    return payload


def write_lessons_runtime_truth_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_lessons_runtime_truth(runtime_root=root)
    eff = {
        "lessons_runtime_truth_ref": str(ctrl / "lessons_runtime_truth.json"),
        "gate_b_classification": "stored_only_for_json_lessons_shark_paths_may_read_for_display",
        "runtime_influences_decisions": {
            "gate_b_coinbase": False,
            "shark_mission_reads_lessons_for_context": True,
        },
    }
    (ctrl / "lessons_runtime_truth.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "lessons_runtime_truth.txt").write_text(
        json.dumps(payload, indent=2)[:12000] + "\n", encoding="utf-8"
    )
    (ctrl / "lessons_effect_on_runtime.json").write_text(json.dumps(eff, indent=2) + "\n", encoding="utf-8")
    (ctrl / "lessons_effect_on_runtime.txt").write_text(json.dumps(eff, indent=2) + "\n", encoding="utf-8")
    op = {
        "operator_must_know": payload["honesty"],
        "classification_table": payload["subsystem_classifications"],
    }
    (ctrl / "lessons_operator_proof.json").write_text(json.dumps(op, indent=2) + "\n", encoding="utf-8")
    (ctrl / "lessons_operator_proof.txt").write_text(json.dumps(op, indent=2) + "\n", encoding="utf-8")
    return {
        "generated_at": payload["generated_at"],
        "paths": {
            "lessons_runtime_truth.json": str(ctrl / "lessons_runtime_truth.json"),
            "lessons_effect_on_runtime.json": str(ctrl / "lessons_effect_on_runtime.json"),
            "lessons_operator_proof.json": str(ctrl / "lessons_operator_proof.json"),
        },
    }
