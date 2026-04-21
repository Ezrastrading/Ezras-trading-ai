"""
Recommendation → implementation → outcome tracking (append-only JSONL + memory merge).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.learning.paths import improvement_history_path
from trading_ai.learning import trading_memory as tm


def _append_jsonl(record: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or improvement_history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


def extract_lessons_from_diagnosis(diagnosis: Mapping[str, Any]) -> List[str]:
    lessons: List[str] = []
    m = diagnosis.get("metrics") or {}
    rr = diagnosis.get("risk_recommendation") or {}
    if float(m.get("avg_slippage_bps") or 0) > 30:
        lessons.append("slippage elevated — check book depth and sizing in current regime")
    for vk, agg in (m.get("venue_performance") or {}).items():
        if isinstance(agg, dict) and float(agg.get("net_pnl") or 0) < 0:
            lessons.append(f"venue {vk} negative on the day — review edge vs conditions")
    if rr.get("risk_mode") == "lower_risk":
        lessons.append("risk too high after loss streak or negative expectancy — defensive posture")
    if int(m.get("anomaly_count") or 0) >= 5:
        lessons.append("execution anomalies clustering — inspect connectivity and sizing")
    if not lessons:
        lessons.append("no strong lesson extracted — maintain measurement discipline")
    return lessons


def ingest_daily_diagnosis(diagnosis: Mapping[str, Any]) -> Dict[str, Any]:
    """Update trading_memory.json and append improvement_history.jsonl."""
    mem = tm.load_trading_memory()
    lessons = extract_lessons_from_diagnosis(diagnosis)
    for les in lessons[:20]:
        tm.append_unique(mem["edge_improvements"], les)

    probs = diagnosis.get("key_problems") or []
    strs = diagnosis.get("key_strengths") or []
    for p in probs[:5]:
        tm.append_unique(mem["repeated_mistakes"], str(p))
    for s in strs[:5]:
        tm.append_unique(mem["repeated_strengths"], str(s))

    vm = diagnosis.get("metrics") or {}
    for vk, agg in (vm.get("venue_performance") or {}).items():
        if isinstance(agg, dict):
            vid = str(vk).lower()
            if vid in mem["avenue_summaries"]:
                if float(agg.get("net_pnl") or 0) >= 0:
                    tm.append_unique(mem["avenue_summaries"][vid]["what_works"], f"day pnl ok: {agg}")
                else:
                    tm.append_unique(mem["avenue_summaries"][vid]["what_fails"], f"day pnl weak: {agg}")

    ceo_action = {
        "date": diagnosis.get("date"),
        "recommended_actions": diagnosis.get("recommended_actions"),
        "risk": diagnosis.get("risk_recommendation"),
    }
    hist = mem.get("ceo_recommendations_history")
    if isinstance(hist, list):
        hist.insert(0, ceo_action)
        del hist[100:]

    tm.save_trading_memory(mem)

    _append_jsonl(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "daily_diagnosis_ingest",
            "date": diagnosis.get("date"),
            "lessons": lessons,
            "health": diagnosis.get("health"),
        }
    )
    return {"ingested": True, "lessons": lessons}


def record_implementation(
    recommendation_id: str,
    *,
    implemented: bool,
    notes: str = "",
) -> None:
    _append_jsonl(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "implementation",
            "recommendation_id": recommendation_id,
            "implemented": implemented,
            "notes": notes,
        }
    )


def record_outcome(
    recommendation_id: str,
    *,
    outcome: str,
    worked: Optional[bool] = None,
) -> None:
    _append_jsonl(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "outcome",
            "recommendation_id": recommendation_id,
            "outcome": outcome,
            "worked": worked,
        }
    )
    mem = tm.load_trading_memory()
    if worked is True:
        tm.append_unique(mem["recommendations_that_worked"], f"{recommendation_id}: {outcome}")
    elif worked is False:
        tm.append_unique(mem["recommendations_that_failed"], f"{recommendation_id}: {outcome}")
    tm.save_trading_memory(mem)


def link_recommendation_outcome(recommendation_id: str, *, success: bool, detail: str = "") -> None:
    """Convenience: mark whether a prior recommendation helped."""
    record_outcome(recommendation_id, outcome=detail or "closed", worked=success)
