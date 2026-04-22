"""
CEO-readable daily review derived from :mod:`trading_ai.review.daily_diagnosis`.

Advisory only — writes JSON + text under ``data/review/``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.review.paths import ceo_daily_review_json_path, ceo_daily_review_txt_path


def _memory_follow_up() -> Dict[str, Any]:
    try:
        from trading_ai.learning.trading_memory import load_trading_memory

        return load_trading_memory()
    except Exception:
        return {}


def build_ceo_daily_review(diagnosis: Mapping[str, Any], *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Higher-level CEO session output: what to improve, avoid, implement, pause, scale;
    risk and discipline narrative; memory cross-reference.
    """
    mem = _memory_follow_up()
    repeating = mem.get("repeated_mistakes") or []
    strengths_m = mem.get("repeated_strengths") or []
    worked = mem.get("recommendations_that_worked") or []
    failed = mem.get("recommendations_that_failed") or []

    metrics = diagnosis.get("metrics") or {}
    rr = diagnosis.get("risk_recommendation") or {}
    dr = diagnosis.get("discipline_recommendation") or {}

    what_improve: List[str] = []
    what_avoid: List[str] = []
    if diagnosis.get("key_problems"):
        what_improve.extend(str(x) for x in diagnosis["key_problems"][:10])
    what_improve.append("Keep post-fee expectancy and execution quality visible in every venue slice.")
    if float(metrics.get("fees_to_pnl_ratio") or 0) > 0.35:
        what_avoid.append("Oversized activity when fees dominate net.")
    if int(metrics.get("anomaly_count") or 0) >= 3:
        what_avoid.append("Trading through repeated execution anomalies without root-cause.")
    what_avoid.append("Chasing frequency without edge confirmation.")

    pause: List[str] = []
    scale: List[str] = []
    if rr.get("risk_mode") == "lower_risk":
        pause.append("Increase size or new strategy tests until metrics recover.")
    if rr.get("risk_mode") == "raise_risk" and dr.get("posture") == "slightly_more_aggression_allowed":
        scale.append("Validated edges with clean execution — consider gradual size up.")
    if diagnosis.get("health") == "bad":
        pause.append("Discretionary adds outside governance checks.")

    implement_next: List[str] = [
        "Refresh venue-level execution dashboards after anomaly spikes.",
        "Reconcile edge registry vs measured post-fee truth weekly.",
    ]
    if repeating:
        implement_next.append(f"Address repeating issue: {repeating[0]}")

    memory_note = []
    if repeating:
        memory_note.append(f"Repeating problem: {repeating[0]}")
    if strengths_m:
        memory_note.append(f"Repeating strength: {strengths_m[0]}")
    if worked:
        memory_note.append(f"Prior fix worked: {worked[0]}")
    if failed:
        memory_note.append(f"Prior fix underperformed: {failed[0]}")

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": diagnosis.get("date"),
        "executive_summary": {
            "health": diagnosis.get("health"),
            "biggest_risk": diagnosis.get("biggest_risk"),
            "best_opportunity": diagnosis.get("best_opportunity"),
        },
        "what_to_improve": what_improve,
        "what_to_avoid": what_avoid,
        "what_to_implement_next": implement_next,
        "what_to_pause": pause,
        "what_to_scale": scale,
        "where_risk_is_too_high": diagnosis.get("biggest_risk") if rr.get("risk_mode") == "lower_risk" else None,
        "where_discipline_is_slipping": diagnosis.get("key_problems", [])[:3]
        if dr.get("posture") == "tighten_discipline"
        else [],
        "where_edge_is_strengthening": diagnosis.get("key_strengths", [])[:3],
        "risk": rr,
        "discipline": dr,
        "memory_follow_up": memory_note,
        "recommended_actions": diagnosis.get("recommended_actions") or [],
        "external_context": diagnosis.get("external_context") or [],
        "possible_market_explanation": diagnosis.get("possible_market_explanation") or [],
    }
    try:
        from trading_ai.control.first_60_day_ops import attach_first_60_context_for_ceo_review

        f60 = attach_first_60_context_for_ceo_review(diagnosis, runtime_root=runtime_root)
        if f60.get("active"):
            out["first_60_day_live_operations"] = f60
    except Exception:
        pass
    return out


def _txt_report(payload: Mapping[str, Any]) -> str:
    lines: List[str] = []
    lines.append("CEO DAILY REVIEW")
    lines.append("================")
    lines.append(f"Date: {payload.get('date')}")
    lines.append(f"Generated: {payload.get('generated_at')}")
    ex = payload.get("executive_summary") or {}
    lines.append(f"Health: {ex.get('health')}")
    lines.append(f"Biggest risk: {ex.get('biggest_risk')}")
    lines.append(f"Best opportunity: {ex.get('best_opportunity')}")
    lines.append("")
    lines.append("WHAT TO IMPROVE")
    for x in payload.get("what_to_improve") or []:
        lines.append(f"  - {x}")
    lines.append("")
    lines.append("WHAT TO AVOID")
    for x in payload.get("what_to_avoid") or []:
        lines.append(f"  - {x}")
    lines.append("")
    lines.append("IMPLEMENT NEXT")
    for x in payload.get("what_to_implement_next") or []:
        lines.append(f"  - {x}")
    lines.append("")
    lines.append("PAUSE / SCALE")
    lines.append("  Pause:")
    for x in payload.get("what_to_pause") or []:
        lines.append(f"    - {x}")
    lines.append("  Scale:")
    for x in payload.get("what_to_scale") or []:
        lines.append(f"    - {x}")
    lines.append("")
    lines.append("RISK / DISCIPLINE")
    lines.append(f"  {payload.get('risk')}")
    lines.append(f"  {payload.get('discipline')}")
    lines.append("")
    lines.append("MEMORY")
    for x in payload.get("memory_follow_up") or []:
        lines.append(f"  - {x}")
    lines.append("")
    f60 = payload.get("first_60_day_live_operations")
    if isinstance(f60, dict) and f60.get("active"):
        lines.append("FIRST 60-DAY LIVE OPS (CONTROL)")
        lines.append(f"  live_day: {f60.get('calendar_day_since_live_start')}")
        lines.append(f"  phase: {f60.get('phase_label')}")
        lines.append(f"  objective: {f60.get('objective_today')}")
        lines.append(f"  max_notional_usd (plan): {f60.get('max_open_notional_usd_aggregate')}")
        lines.append(f"  max_trades (plan): {f60.get('max_trades')}")
        lines.append("")
    return "\n".join(lines)


def write_ceo_daily_review(diagnosis: Mapping[str, Any], *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    payload = build_ceo_daily_review(diagnosis, runtime_root=runtime_root)
    jp = ceo_daily_review_json_path()
    tp = ceo_daily_review_txt_path()
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tp.write_text(_txt_report(payload), encoding="utf-8")
    return payload


def run_ceo_review_session(
    diagnosis: Optional[Mapping[str, Any]] = None,
    *,
    as_of: Optional[date] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Standalone entry: load latest diagnosis file or build fresh."""
    from trading_ai.review.daily_diagnosis import run_daily_diagnosis
    from trading_ai.review.paths import ceo_daily_review_json_path, daily_diagnosis_path

    if diagnosis is not None:
        return write_ceo_daily_review(diagnosis, runtime_root=runtime_root)
    p = daily_diagnosis_path()
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return write_ceo_daily_review(d, runtime_root=runtime_root)
        except (json.JSONDecodeError, OSError):
            pass
    run_daily_diagnosis(as_of=as_of, write_files=True)
    cp = ceo_daily_review_json_path()
    if cp.is_file():
        try:
            raw = json.loads(cp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (json.JSONDecodeError, OSError):
            pass
    return {}
