"""
Daily CEO **ratio** review session — one file pair per day under data/review.

Reuses the same directory as generic CEO review but is **ratio-specific**.
Does **not** call external LLM APIs; it aggregates grounded artifacts only.
For narrative CEO review, see :mod:`trading_ai.review.ceo_review_session`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.ratios.universal_ratio_registry import build_universal_ratio_policy_bundle


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def build_daily_ratio_review_payload(
    *,
    runtime_root: Path,
    review_date: Optional[str] = None,
) -> Dict[str, Any]:
    bundle = build_universal_ratio_policy_bundle()
    ctrl = runtime_root / "data" / "control"
    dcr = _read_json(ctrl / "deployable_capital_report.json") or {}
    rsv = _read_json(ctrl / "reserve_capital_report.json") or {}
    rps = _read_json(ctrl / "ratio_policy_snapshot.json") or {}

    rd = review_date or str(date.today())

    recommended: List[Dict[str, Any]] = []
    do_not_change: List[str] = [
        "universal.max_daily_drawdown_ratio",
        "universal.per_trade_cap_fraction",
    ]
    open_questions: List[str] = [
        "Are hard/soft reserve ratios appropriate for current portfolio volatility?",
        "Should Gate B momentum_safe_deployable_fraction track adaptive OS output directly?",
    ]

    # Honest scaffold flags
    scaffold = []
    for k, v in (bundle.universal_ratios or {}).items():
        if str((v or {}).get("notes") or "").find("scaffold") >= 0:
            scaffold.append(k)

    return {
        "session": "CEO_DAILY_RATIO_REVIEW",
        "version": "v1",
        "review_date": rd,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "ratio_policy_snapshot_present": bool(rps),
            "deployable_capital_report_present": bool(dcr),
            "reserve_capital_report_present": bool(rsv),
        },
        "active_universal_ratios": {k: v.get("value") for k, v in bundle.universal_ratios.items()},
        "avenue_overlays": bundle.avenue_overlays,
        "gate_overlays": bundle.gate_overlays,
        "deployable_summary": {
            "conservative": dcr.get("conservative_deployable_capital"),
            "portfolio_mark": dcr.get("portfolio_total_mark_value_usd"),
        },
        "reserve_summary": {
            "reserved_total": rsv.get("reserved_capital_total"),
            "deployable_after_reserves": rsv.get("deployable_after_reserves"),
        },
        "recommended_ratio_adjustments": recommended,
        "confidence_of_adjustment": 0.0,
        "do_not_change": do_not_change,
        "open_questions": open_questions,
        "scaffold_only_ratio_keys": scaffold,
        "orchestration_note": (
            "No Claude/GPT call in this module — grounded JSON only. "
            "Optional: paste this JSON into an external CEO workflow."
        ),
        "llm_orchestration_status": "not_yet_wired",
        "next_integration_step_for_llm_review": (
            "If dual-LLM CEO ratio review is required, add an explicit orchestrator module or external scheduler; "
            "this file remains the grounded artifact source."
        ),
    }


def write_daily_ratio_review(runtime_root: Path) -> Dict[str, str]:
    rev = runtime_root / "data" / "review"
    rev.mkdir(parents=True, exist_ok=True)
    payload = build_daily_ratio_review_payload(runtime_root=runtime_root)
    jp = rev / "daily_ratio_review.json"
    tp = rev / "daily_ratio_review.txt"
    js = json.dumps(payload, indent=2, default=str)
    jp.write_text(js, encoding="utf-8")
    tp.write_text(
        "\n".join(
            [
                "CEO DAILY RATIO REVIEW (grounded)",
                "================================",
                js[:22000],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"daily_ratio_review_json": str(jp), "daily_ratio_review_txt": str(tp)}
