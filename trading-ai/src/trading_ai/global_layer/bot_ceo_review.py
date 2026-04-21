"""CEO integration for bot governance — low-token structured summary (deterministic)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from trading_ai.global_layer.audit_trail import append_audit_event
from trading_ai.global_layer.bot_registry import load_registry
from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review
from trading_ai.global_layer.orchestration_schema import MAX_BOTS_PER_AVENUE
from trading_ai.global_layer.bot_scoring import default_metric_schema, merge_metrics_update
from trading_ai.global_layer.budget_governor import record_ai_call


def review_all_bots(*, registry_path=None) -> Dict[str, Any]:
    reg = load_registry(registry_path)
    bots = list(reg.get("bots") or [])
    reviews: List[Dict[str, Any]] = []
    request_new = False
    for b in bots:
        perf = dict(b.get("performance") or {})
        metrics = merge_metrics_update(default_metric_schema(), perf)
        util = float((metrics.get("composite") or {}).get("utility_score") or 0.0)
        rec: str
        if util >= 0.55:
            rec = "keep"
        elif util >= 0.35:
            rec = "improve"
        elif util >= 0.15:
            rec = "replace"
        else:
            rec = "remove"
            request_new = True
        reviews.append(
            {
                "bot_id": b.get("bot_id"),
                "role": b.get("role"),
                "avenue": b.get("avenue"),
                "gate": b.get("gate"),
                "recommendation": rec,
                "utility_score": util,
            }
        )
    out = {
        "truth_version": "bot_ceo_review_v1",
        "bot_count": len(bots),
        "reviews": reviews,
        "request_new_bot": request_new,
        "max_bots_per_avenue_policy": MAX_BOTS_PER_AVENUE,
        "daily_review_artifact": None,
    }
    try:
        daily = write_daily_ceo_review(registry_path=registry_path)
        out["daily_review_artifact"] = daily.get("truth_version")
    except Exception:
        pass
    append_audit_event(
        "ceo_bot_review",
        out,
        bot_id="CEO",
        approved_by=None,
        evidence_refs=["bot_registry"],
    )
    record_ai_call(tokens=0, call_kind="ceo_bot_review")
    return out


def format_ceo_bot_markdown(summary: Dict[str, Any]) -> str:
    lines = ["### Bot governance (deterministic)", ""]
    for r in summary.get("reviews") or []:
        lines.append(
            f"- **{r.get('bot_id')}** ({r.get('role')} / {r.get('avenue')} / {r.get('gate')}): "
            f"**{r.get('recommendation')}** (utility≈{r.get('utility_score')})"
        )
    if summary.get("request_new_bot"):
        lines.append("- **request_new_bot**: gap detected — run factory with measured context (not auto-spawn).")
    lines.append("")
    return "\n".join(lines)


def build_ceo_bot_section_for_session(context: Dict[str, Any]) -> str:
    """Optional hook: pass registry path via context['bot_registry_path']."""
    path = context.get("bot_registry_path")
    summ = review_all_bots(registry_path=path)
    return format_ceo_bot_markdown(summ)
