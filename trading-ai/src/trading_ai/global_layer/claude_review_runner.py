"""Run Claude review — production prompts, schema validation, repair, retry."""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.review_policy import load_policy_from_environ
from trading_ai.global_layer.review_prompts import CLAUDE_SYSTEM_PROMPT, REPAIR_PROMPT, claude_user_prompt
from trading_ai.global_layer.review_retry_policy import MAX_JSON_REPAIR_ATTEMPTS, MAX_MODEL_CALL_RETRIES
from trading_ai.global_layer.review_schema import extract_json_dict, strip_internal_keys, validate_claude_output
from trading_ai.global_layer.review_storage import ReviewStorage

logger = logging.getLogger(__name__)


def _default_claude_out(packet_id: str, review_type: str, stub: bool) -> Dict[str, Any]:
    rid = f"cl_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return {
        "review_id": rid,
        "packet_id": packet_id,
        "review_type": review_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "what_is_working": ["insufficient_packet_evidence"] if stub else ["review_stub_pending_api"],
        "what_is_not_working": [],
        "biggest_risk_now": "unknown_without_live_data",
        "most_fragile_part_of_system": "data_pipeline_or_connectivity",
        "best_safe_improvement": "tighten_monitoring_and_verify_writes",
        "worst_live_behavior_to_cut": "none_identified",
        "best_shadow_candidate_to_watch": "none_identified",
        "capital_preservation_note": "keep_size_small_until_edge_proven",
        "path_to_first_million_note": "compound_validated_post_fee_expectancy_only",
        "risk_mode_recommendation": "caution",
        "confidence_score": 0.35,
        "stub": stub,
        "_validation_ok": True,
        "_repair_used": False,
    }


def _apply_ids(parsed: Dict[str, Any], pid: str, rtype: str) -> None:
    parsed.setdefault("review_id", f"cl_{uuid.uuid4().hex[:12]}")
    parsed["packet_id"] = pid
    parsed["review_type"] = rtype
    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()


def _anthropic_messages_create(client: Any, policy: Any, user_text: str, repair_extra: str = "") -> str:
    body = user_text + (("\n\n" + repair_extra) if repair_extra else "")
    kwargs: Dict[str, Any] = {
        "model": policy.claude_model,
        "max_tokens": 1600,
        "system": CLAUDE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": body}],
    }
    try:
        msg = client.messages.create(**kwargs, timeout=policy.model_timeout_sec)
    except TypeError:
        msg = client.messages.create(**kwargs)
    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text
    return text


def run_claude_review(
    packet: Dict[str, Any],
    *,
    storage: Optional[ReviewStorage] = None,
    force_stub: bool = False,
) -> Dict[str, Any]:
    policy = load_policy_from_environ()
    st = storage or ReviewStorage()
    pid = str(packet.get("packet_id") or "unknown")
    rtype = str(packet.get("review_type") or "morning")
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    if force_stub or (not key and policy.stub_if_no_api_key):
        out = _default_claude_out(pid, rtype, stub=True)
        st.save_json("claude_review_latest.json", strip_internal_keys(out))
        st.append_jsonl(
            "claude_review_history.jsonl",
            {"ts": time.time(), "review_id": out["review_id"], "stub": True, "validation_ok": True},
        )
        return out

    import anthropic

    client = anthropic.Anthropic(api_key=key)
    user_text = claude_user_prompt(packet)
    if len(user_text) > policy.max_packet_chars + 5000:
        user_text = user_text[: policy.max_packet_chars + 5000]

    last_err: Optional[str] = None
    text_out = ""
    for attempt in range(MAX_MODEL_CALL_RETRIES + 1):
        try:
            text_out = _anthropic_messages_create(client, policy, user_text)
            break
        except Exception as exc:
            last_err = str(exc)
            logger.warning("Claude API attempt %s failed: %s", attempt, exc)
            if attempt >= MAX_MODEL_CALL_RETRIES:
                out = _default_claude_out(pid, rtype, stub=True)
                out["error"] = (last_err or "unknown")[:200]
                out["_validation_ok"] = False
                out["_validation_errors"] = ["api_failure"]
                st.save_json("claude_review_latest.json", strip_internal_keys(out))
                st.append_jsonl(
                    "claude_review_history.jsonl",
                    {"ts": time.time(), "error": out.get("error"), "stub": True, "validation_ok": False},
                )
                return out

    parsed = extract_json_dict(text_out)
    repair_used = False
    errs: List[str] = []
    if parsed:
        _apply_ids(parsed, pid, rtype)
        ok, errs = validate_claude_output(parsed, packet_id=pid, review_type=rtype)
        parsed["_validation_ok"] = ok
        if not ok and MAX_JSON_REPAIR_ATTEMPTS:
            repair_used = True
            text2 = _anthropic_messages_create(
                client,
                policy,
                user_text + "\n\n" + REPAIR_PROMPT + "\n\nPrevious (invalid):\n" + text_out[:6000],
            )
            parsed2 = extract_json_dict(text2)
            if parsed2:
                _apply_ids(parsed2, pid, rtype)
                ok2, errs2 = validate_claude_output(parsed2, packet_id=pid, review_type=rtype)
                parsed2["_validation_ok"] = ok2
                parsed2["_repair_used"] = repair_used
                if ok2:
                    parsed = parsed2
                    errs = []
                else:
                    errs = errs2
            else:
                errs.append("repair_parse_failed")

    if not parsed or not parsed.get("_validation_ok"):
        out = _default_claude_out(pid, rtype, stub=True)
        out["_validation_ok"] = False
        out["_validation_errors"] = errs or ["invalid_json_or_schema"]
        out["_repair_used"] = repair_used
        out["error"] = "validation_failed"
        st.save_json("claude_review_latest.json", strip_internal_keys(out))
        st.append_jsonl(
            "claude_review_history.jsonl",
            {"ts": time.time(), "review_id": out.get("review_id"), "stub": True, "validation_ok": False},
        )
        return out

    parsed["stub"] = False
    parsed["_repair_used"] = repair_used
    st.save_json("claude_review_latest.json", strip_internal_keys(parsed))
    st.append_jsonl(
        "claude_review_history.jsonl",
        {"ts": time.time(), "review_id": parsed.get("review_id"), "stub": False, "validation_ok": True},
    )
    return parsed
