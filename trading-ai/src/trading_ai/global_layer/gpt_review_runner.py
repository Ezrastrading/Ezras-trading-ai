"""Run GPT review — production prompts, schema validation, repair, retry."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.review_policy import load_policy_from_environ
from trading_ai.global_layer.review_prompts import GPT_SYSTEM_PROMPT, REPAIR_PROMPT, gpt_user_prompt
from trading_ai.global_layer.review_retry_policy import MAX_JSON_REPAIR_ATTEMPTS, MAX_MODEL_CALL_RETRIES
from trading_ai.global_layer.review_schema import extract_json_dict, strip_internal_keys, validate_gpt_output
from trading_ai.global_layer.review_storage import ReviewStorage

logger = logging.getLogger(__name__)


def _default_gpt_out(packet_id: str, review_type: str, stub: bool) -> Dict[str, Any]:
    rid = f"gpt_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return {
        "review_id": rid,
        "packet_id": packet_id,
        "review_type": review_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_3_decisions": ["verify_data_writes", "keep_risk_clamped", "review_route_mix"],
        "top_3_warnings": ["insufficient_live_sample"],
        "top_3_next_actions": ["run_dashboard", "check_fee_snapshot", "confirm_ws_health"],
        "live_status_recommendation": "caution",
        "best_live_edge_now": "",
        "weakest_live_edge_now": "",
        "best_growth_opportunity": "validated_edge_only",
        "main_bottleneck_to_first_million": "evidence_and_scale",
        "short_ceo_note": "Diagnostic mode: prioritize truth over notional PnL.",
        "confidence_score": 0.4,
        "stub": stub,
        "_validation_ok": True,
        "_repair_used": False,
    }


def _apply_ids(parsed: Dict[str, Any], pid: str, rtype: str) -> None:
    parsed.setdefault("review_id", f"gpt_{uuid.uuid4().hex[:12]}")
    parsed["packet_id"] = pid
    parsed["review_type"] = rtype
    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()


def run_gpt_review(
    packet: Dict[str, Any],
    *,
    storage: Optional[ReviewStorage] = None,
    force_stub: bool = False,
) -> Dict[str, Any]:
    policy = load_policy_from_environ()
    st = storage or ReviewStorage()
    pid = str(packet.get("packet_id") or "unknown")
    rtype = str(packet.get("review_type") or "morning")
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()

    if force_stub or (not key and policy.stub_if_no_api_key):
        out = _default_gpt_out(pid, rtype, stub=True)
        st.save_json("gpt_review_latest.json", strip_internal_keys(out))
        st.append_jsonl(
            "gpt_review_history.jsonl",
            {"ts": time.time(), "review_id": out["review_id"], "stub": True, "validation_ok": True},
        )
        return out

    from openai import OpenAI

    client = OpenAI(api_key=key)
    user = gpt_user_prompt(packet)
    if len(user) > policy.max_packet_chars + 5000:
        user = user[: policy.max_packet_chars + 5000]

    text_out = ""
    last_err: Optional[str] = None
    for attempt in range(MAX_MODEL_CALL_RETRIES + 1):
        try:
            comp = client.chat.completions.create(
                model=policy.gpt_model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": GPT_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                timeout=policy.model_timeout_sec,
            )
            text_out = comp.choices[0].message.content or ""
            break
        except Exception as exc:
            last_err = str(exc)
            logger.warning("GPT API attempt %s failed: %s", attempt, exc)
            if attempt >= MAX_MODEL_CALL_RETRIES:
                out = _default_gpt_out(pid, rtype, stub=True)
                out["error"] = (last_err or "unknown")[:200]
                out["_validation_ok"] = False
                out["_validation_errors"] = ["api_failure"]
                st.save_json("gpt_review_latest.json", strip_internal_keys(out))
                st.append_jsonl(
                    "gpt_review_history.jsonl",
                    {"ts": time.time(), "error": out.get("error"), "stub": True, "validation_ok": False},
                )
                return out

    parsed = extract_json_dict(text_out)
    repair_used = False
    errs: List[str] = []
    if parsed:
        _apply_ids(parsed, pid, rtype)
        ok, errs = validate_gpt_output(parsed, packet_id=pid, review_type=rtype)
        parsed["_validation_ok"] = ok
        if not ok and MAX_JSON_REPAIR_ATTEMPTS:
            repair_used = True
            comp2 = client.chat.completions.create(
                model=policy.gpt_model,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": GPT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": user + "\n\n" + REPAIR_PROMPT + "\n\nPrevious (invalid):\n" + text_out[:6000],
                    },
                ],
                timeout=policy.model_timeout_sec,
            )
            text2 = comp2.choices[0].message.content or ""
            parsed2 = extract_json_dict(text2)
            if parsed2:
                _apply_ids(parsed2, pid, rtype)
                ok2, errs2 = validate_gpt_output(parsed2, packet_id=pid, review_type=rtype)
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
        out = _default_gpt_out(pid, rtype, stub=True)
        out["_validation_ok"] = False
        out["_validation_errors"] = errs or ["invalid_json_or_schema"]
        out["_repair_used"] = repair_used
        out["error"] = "validation_failed"
        st.save_json("gpt_review_latest.json", strip_internal_keys(out))
        st.append_jsonl(
            "gpt_review_history.jsonl",
            {"ts": time.time(), "review_id": out.get("review_id"), "stub": True, "validation_ok": False},
        )
        return out

    parsed["stub"] = False
    parsed["_repair_used"] = repair_used
    st.save_json("gpt_review_latest.json", strip_internal_keys(parsed))
    st.append_jsonl(
        "gpt_review_history.jsonl",
        {"ts": time.time(), "review_id": parsed.get("review_id"), "stub": False, "validation_ok": True},
    )
    return parsed
