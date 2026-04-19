"""Schedule morning / midday / EOD / exception AI reviews — dedupe, thresholds."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.ai_review_packet_builder import build_review_packet, persist_packet
from trading_ai.global_layer.claude_review_runner import run_claude_review
from trading_ai.global_layer.gpt_review_runner import run_gpt_review
from trading_ai.global_layer.ceo_review_writer import attach_ceo_summary_to_joint
from trading_ai.global_layer.joint_review_merger import merge_reviews
from trading_ai.global_layer.review_action_router import route_safe_actions
from trading_ai.global_layer.review_policy import ReviewPolicy, load_policy_from_environ
from trading_ai.global_layer.review_storage import ReviewStorage


def _hour_utc() -> int:
    return datetime.now(timezone.utc).hour


def _count_reviews_today(st: ReviewStorage) -> int:
    """Rough count from history file tail (best-effort)."""
    p = st.store.path("joint_review_history.jsonl")
    if not p.is_file():
        return 0
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return sum(1 for ln in lines if today in ln)
    except OSError:
        return 0


def should_run_morning(policy: ReviewPolicy, st: ReviewStorage) -> bool:
    if not policy.enable_morning_review:
        return False
    state = st.load_json("review_scheduler_state.json")
    last = state.get("last_morning_ts")
    now = time.time()
    if last and (now - float(last)) < 3600 * 20:
        return False
    # 05–10 UTC window as generic "morning"
    h = _hour_utc()
    return 5 <= h <= 10


def should_run_midday(
    policy: ReviewPolicy,
    st: ReviewStorage,
    *,
    closed_trades_recent: int,
    shadow_count: int,
    anomaly_count: int,
) -> bool:
    if not policy.enable_midday_review:
        return False
    state = st.load_json("review_scheduler_state.json")
    last = state.get("last_midday_ts")
    now = time.time()
    if last and (now - float(last)) < 3600 * 3:
        return False
    if closed_trades_recent >= policy.midday_min_closed_trades:
        return True
    if shadow_count >= policy.midday_min_shadow_candidates:
        return True
    if anomaly_count >= policy.midday_min_anomaly_count:
        return True
    return False


def should_run_eod(policy: ReviewPolicy, st: ReviewStorage) -> bool:
    if not policy.enable_eod_review:
        return False
    state = st.load_json("review_scheduler_state.json")
    last = state.get("last_eod_ts")
    now = time.time()
    if last and (now - float(last)) < 3600 * 20:
        return False
    h = _hour_utc()
    return 21 <= h <= 23


def run_full_review_cycle(
    review_type: str,
    *,
    storage: Optional[ReviewStorage] = None,
    policy: Optional[ReviewPolicy] = None,
    skip_models: bool = False,
) -> Dict[str, Any]:
    """
    Build packet → Claude → GPT → joint → safe actions.

    ``skip_models`` forces stub path without API (for tests).
    """
    policy = policy or load_policy_from_environ()
    st = storage or ReviewStorage()
    if _count_reviews_today(st) >= policy.max_reviews_per_day:
        return {"skipped": True, "reason": "max_reviews_per_day"}

    packet = build_review_packet(review_type=review_type, storage=st, policy=policy)
    persist_packet(packet, storage=st)
    cl = run_claude_review(packet, storage=st, force_stub=skip_models)
    gp = run_gpt_review(packet, storage=st, force_stub=skip_models)
    joint = merge_reviews(packet, cl, gp, storage=st)
    joint = attach_ceo_summary_to_joint(joint, packet, review_type)
    st.save_json(
        "joint_review_latest.json",
        {k: v for k, v in joint.items() if not str(k).startswith("_")},
    )
    route_safe_actions(joint, storage=st, policy=policy, packet=packet)

    snap = st.load_json("review_policy_snapshot.json")
    snap["snapshot"] = policy.to_dict()
    st.save_json("review_policy_snapshot.json", snap)

    now = time.time()
    sched = st.load_json("review_scheduler_state.json")
    if review_type == "morning":
        sched["last_morning_ts"] = now
    elif review_type == "midday":
        sched["last_midday_ts"] = now
    elif review_type == "eod":
        sched["last_eod_ts"] = now
    elif review_type == "exception":
        sched["last_exception_ts"] = now
    st.save_json("review_scheduler_state.json", sched)

    return {"packet": packet, "claude": cl, "gpt": gp, "joint": joint}


def tick_scheduler(storage: Optional[ReviewStorage] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Call periodically (e.g. cron). Returns list of (review_type, result) actually run.
    """
    policy = load_policy_from_environ()
    st = storage or ReviewStorage()
    out: List[Tuple[str, Dict[str, Any]]] = []

    pkt = st.load_json("review_packet_latest.json")
    lt = pkt.get("live_trading_summary") or {}
    closed = int(lt.get("closed_trades_count") or 0)
    sh = st.load_json("candidate_queue.json")
    shadow_n = len(sh.get("items") or [])
    anom = int((pkt.get("risk_summary") or {}).get("loss_cluster_count") or 0)

    if should_run_morning(policy, st):
        out.append(("morning", run_full_review_cycle("morning", storage=st, policy=policy)))
    if should_run_midday(policy, st, closed_trades_recent=closed, shadow_count=shadow_n, anomaly_count=anom):
        out.append(("midday", run_full_review_cycle("midday", storage=st, policy=policy)))
    if should_run_eod(policy, st):
        out.append(("eod", run_full_review_cycle("eod", storage=st, policy=policy)))
    return out
