"""Central policy for AI review cadence, thresholds, and safe actions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ReviewPolicy:
    enable_morning_review: bool = True
    enable_midday_review: bool = True
    enable_eod_review: bool = True
    enable_exception_review: bool = True

    midday_min_closed_trades: int = 3
    midday_min_shadow_candidates: int = 5
    midday_min_anomaly_count: int = 1

    exception_trigger_hard_stop: bool = True
    exception_trigger_write_failure: bool = True
    exception_trigger_slippage_cluster: bool = True

    max_reviews_per_day: int = 4
    review_token_budget_class: str = "compact"

    allow_safe_action_router: bool = True
    require_joint_review_for_action: bool = False

    claude_model: str = field(default_factory=lambda: os.environ.get("AI_REVIEW_CLAUDE_MODEL", "claude-sonnet-4-20250514"))
    gpt_model: str = field(default_factory=lambda: os.environ.get("AI_REVIEW_GPT_MODEL", "gpt-4o-mini"))

    max_packet_chars: int = 24_000
    model_timeout_sec: float = 120.0
    stub_if_no_api_key: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enable_morning_review": self.enable_morning_review,
            "enable_midday_review": self.enable_midday_review,
            "enable_eod_review": self.enable_eod_review,
            "enable_exception_review": self.enable_exception_review,
            "midday_min_closed_trades": self.midday_min_closed_trades,
            "midday_min_shadow_candidates": self.midday_min_shadow_candidates,
            "midday_min_anomaly_count": self.midday_min_anomaly_count,
            "exception_trigger_hard_stop": self.exception_trigger_hard_stop,
            "exception_trigger_write_failure": self.exception_trigger_write_failure,
            "exception_trigger_slippage_cluster": self.exception_trigger_slippage_cluster,
            "max_reviews_per_day": self.max_reviews_per_day,
            "review_token_budget_class": self.review_token_budget_class,
            "allow_safe_action_router": self.allow_safe_action_router,
            "require_joint_review_for_action": self.require_joint_review_for_action,
            "claude_model": self.claude_model,
            "gpt_model": self.gpt_model,
            "max_packet_chars": self.max_packet_chars,
            "model_timeout_sec": self.model_timeout_sec,
            "stub_if_no_api_key": self.stub_if_no_api_key,
        }


FORBIDDEN_ACTION_TYPES = frozenset(
    {
        "deploy_new_live_strategy",
        "disable_hard_stop",
        "override_verification_failure",
        "auto_promote_shadow_to_full_live",
        "increase_live_size_beyond_policy",
    }
)

ALLOWED_ACTION_TYPES = frozenset(
    {
        "caution_flag",
        "queue_priority_update",
        "ceo_queue_note",
        "governance_note",
        "recommend_pause",
        "recommend_reduced_live_mode",
        "recommend_shadow_only",
        "request_extra_review",
        "request_manual_attention",
        "route_tightening_suggestion",
        "dashboard_alert_tighten",
    }
)


def load_policy_from_environ() -> ReviewPolicy:
    """Optional env overrides — keep defaults if unset."""

    def _b(key: str, default: bool) -> bool:
        v = (os.environ.get(key) or "").strip().lower()
        if not v:
            return default
        return v in ("1", "true", "yes")

    return ReviewPolicy(
        enable_morning_review=_b("AI_REVIEW_ENABLE_MORNING", True),
        enable_midday_review=_b("AI_REVIEW_ENABLE_MIDDAY", True),
        enable_eod_review=_b("AI_REVIEW_ENABLE_EOD", True),
        enable_exception_review=_b("AI_REVIEW_ENABLE_EXCEPTION", True),
        midday_min_closed_trades=int(os.environ.get("AI_REVIEW_MIDDAY_MIN_TRADES", "3")),
        max_reviews_per_day=int(os.environ.get("AI_REVIEW_MAX_PER_DAY", "4")),
    )
