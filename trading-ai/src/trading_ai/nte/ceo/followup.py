"""CEO session follow-up: open actions, metric baselines, did-we-improve hooks."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from trading_ai.nte.capital_ledger import snapshot_for_goals, weekly_net_for_goals
from trading_ai.nte.ceo.action_tracker import (
    append_action,
    list_open_actions,
    update_action_status,
)
from trading_ai.nte.memory.store import MemoryStore


def metric_baseline() -> Dict[str, Any]:
    """Current numbers to compare against ``expected_effect`` on past actions."""
    snap = snapshot_for_goals()
    return {
        "ts": time.time(),
        "weekly_net_profit_usd": float(snap.get("weekly_net_profit_usd") or weekly_net_for_goals()),
        "equity_estimate_usd": float(snap.get("equity_estimate") or 0.0),
        "realized_pnl_net": float(snap.get("realized_pnl_net") or 0.0),
        "capital_added": float(snap.get("capital_added") or 0.0),
    }


def prepare_ceo_followup_briefing(
    *,
    session_id: str,
    store: Optional[MemoryStore] = None,
) -> Dict[str, Any]:
    """
    Material for the next CEO session: open actions + suggested review prompts.

    Call from :class:`trading_ai.global_layer.briefing_engine.BriefingEngine` or a CEO runner.
    """
    st = store or MemoryStore()
    st.ensure_defaults()
    open_a = list_open_actions()
    base = metric_baseline()
    lines: List[str] = [
        "### CEO action follow-up",
        "",
        f"- Open actions: **{len(open_a)}**",
        f"- Weekly net (ledger): **${base['weekly_net_profit_usd']:.2f}**",
        "",
    ]
    questions: List[str] = []
    for a in open_a[:25]:
        aid = str(a.get("action_id") or "")
        desc = str(a.get("description") or "")[:120]
        metric = str(a.get("metric_to_watch") or "")
        exp = str(a.get("expected_effect") or "")
        lines.append(f"- **{aid[:8]}…** {desc}")
        if metric:
            questions.append(f"Did `{metric}` move as expected for action {aid[:8]}? ({exp})")
    lines.append("")
    lines.append("**Review prompts**")
    for q in questions[:10]:
        lines.append(f"- {q}")

    rev = st.load_json("review_state.json")
    rev["ceo_followup_last"] = {
        "session_id": session_id,
        "ts": time.time(),
        "open_action_count": len(open_a),
        "metric_baseline": base,
    }
    st.save_json("review_state.json", rev)

    return {
        "session_id": session_id,
        "markdown": "\n".join(lines),
        "open_actions": open_a,
        "metric_baseline": base,
        "review_questions": questions,
    }


def record_action_outcome_measured(
    action_id: str,
    *,
    actual_effect: str,
    status: str = "done",
) -> None:
    """Close or update an action after measuring impact (e.g. next CEO session)."""
    update_action_status(action_id, status, actual_effect=actual_effect)


def seed_action_if_absent(
    *,
    session_id: str,
    description: str,
    action_type: str = "process",
    avenue_scope: str = "global",
) -> Optional[str]:
    """Idempotent helper for tests — only append if no matching open description."""
    for a in list_open_actions():
        if str(a.get("description")) == description:
            return str(a.get("action_id"))
    return append_action(
        session_id=session_id,
        avenue_scope=avenue_scope,
        action_type=action_type,
        description=description,
        reason="ceo_followup",
        priority="medium",
        owner_module="nte.ceo.followup",
        metric_to_watch="weekly_net_profit_usd",
        expected_effect="measurable change vs baseline",
    )
