"""Token / AI call budgets — deterministic caps; CEO + periodic learning per product policy."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from trading_ai.global_layer._bot_paths import global_layer_governance_dir

# Policy: align with user requirement — AI calls primarily CEO + every N trades learning
DEFAULT_AI_CALLS_PER_HOUR = 8
DEFAULT_TRADE_INTERVAL_FOR_LEARNING = 20


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def budget_state_path() -> Path:
    return global_layer_governance_dir() / "budget_governor.json"


def load_budget_state() -> Dict[str, Any]:
    p = budget_state_path()
    if not p.is_file():
        return {
            "truth_version": "budget_governor_v1",
            "global_daily_token_budget": 250_000,
            "per_avenue_token_budget": {"A": 80_000, "B": 80_000, "C": 80_000},
            "per_bot_daily_token_budget": 40_000,
            "per_ceo_review_token_budget": 25_000,
            "ceo_review_tokens_used_today": 0,
            "review_day_id": None,
            "ai_calls_this_hour": 0,
            "hour_id": None,
            "trades_since_learning": 0,
            "learning_trade_interval": DEFAULT_TRADE_INTERVAL_FOR_LEARNING,
            "cooldown_until": None,
            "updated_at": None,
        }
    return json.loads(p.read_text(encoding="utf-8"))


def save_budget_state(st: Dict[str, Any]) -> None:
    p = budget_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    st = dict(st)
    st["updated_at"] = _iso()
    p.write_text(json.dumps(st, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_ai_call(*, tokens: int = 0, call_kind: str = "generic") -> Dict[str, Any]:
    st = load_budget_state()
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    if st.get("hour_id") != hour:
        st["hour_id"] = hour
        st["ai_calls_this_hour"] = 0
    st["ai_calls_this_hour"] = int(st.get("ai_calls_this_hour") or 0) + 1
    st["last_call_kind"] = call_kind
    st["last_tokens"] = int(tokens)
    save_budget_state(st)
    return st


def budget_action_for_bot(bot_id: str, requested_tokens: int) -> str:
    st = load_budget_state()
    per = int(st.get("per_bot_daily_token_budget") or 0)
    # Simplified: single bucket check
    used = int((st.get("per_bot_usage") or {}).get(bot_id) or 0)
    if used + requested_tokens > per:
        return "throttle"
    if int(st.get("ai_calls_this_hour") or 0) >= DEFAULT_AI_CALLS_PER_HOUR:
        return "downgrade"
    return "allow"


def record_ceo_review_tokens(tokens: int) -> Dict[str, Any]:
    """Increment daily CEO review usage; resets on UTC day boundary."""
    st = load_budget_state()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if st.get("review_day_id") != day:
        st["review_day_id"] = day
        st["ceo_review_tokens_used_today"] = 0
    cap = int(st.get("per_ceo_review_token_budget") or 25_000)
    used = int(st.get("ceo_review_tokens_used_today") or 0) + int(tokens)
    st["ceo_review_tokens_used_today"] = used
    st["review_budget_exhausted"] = used >= cap
    save_budget_state(st)
    return st


def can_run_ceo_review(*, estimated_tokens: int = 500) -> Tuple[bool, str]:
    st = load_budget_state()
    if bool(st.get("review_budget_exhausted")):
        return False, "ceo_review_budget_flagged_exhausted"
    cap = int(st.get("per_ceo_review_token_budget") or 25_000)
    used = int(st.get("ceo_review_tokens_used_today") or 0)
    if used + estimated_tokens > cap:
        return False, "ceo_review_token_cap"
    return True, "ok"


def can_allocate_bot_slot(*, avenue: str) -> Tuple[bool, str]:
    st = load_budget_state()
    glob = int(st.get("global_daily_token_budget") or 0)
    used = int(st.get("global_token_used") or 0)
    if used >= glob:
        return False, "global_token_cap"
    av = (st.get("per_avenue_token_budget") or {}).get(avenue)
    if av is not None:
        av_used = int((st.get("per_avenue_usage") or {}).get(avenue) or 0)
        if av_used >= int(av):
            return False, "avenue_token_cap"
    return True, "ok"


def should_run_learning_after_trade() -> Tuple[bool, Dict[str, Any]]:
    """Every N trades — deterministic tick (caller increments)."""
    st = load_budget_state()
    n = int(st.get("learning_trade_interval") or DEFAULT_TRADE_INTERVAL_FOR_LEARNING)
    c = int(st.get("trades_since_learning") or 0) + 1
    st["trades_since_learning"] = c
    save_budget_state(st)
    return (c % n == 0), st


def fallback_deterministic_mode() -> bool:
    st = load_budget_state()
    return bool(st.get("force_deterministic") is True)
