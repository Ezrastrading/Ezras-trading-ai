"""
Evidence-first Avenue A coordination truth.

Purpose: one place to answer "what happened last cycle" across Gate A/Gate B and support bots,
without faking activity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_VERSION = "avenue_a_coordination_truth_v1"
_REL = "data/control/avenue_a_coordination_truth.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(ad: LocalStorageAdapter, rel: str) -> Dict[str, Any]:
    try:
        j = ad.read_json(rel)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def build_avenue_a_coordination_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = LocalStorageAdapter(runtime_root=root)

    rr = _read(ad, "data/control/runtime_runner_last_cycle.json")
    cycle = rr.get("avenue_a_daemon") if isinstance(rr.get("avenue_a_daemon"), dict) else {}
    cycle = cycle if isinstance(cycle, dict) else {}
    lv = cycle.get("live_validation") if isinstance(cycle.get("live_validation"), dict) else {}
    lane = cycle.get("autonomous_execution_lane") if isinstance(cycle.get("autonomous_execution_lane"), dict) else {}

    gate = str(lv.get("selected_gate") or "")
    trade_id = str(lv.get("trade_id") or "")

    ga = _read(ad, "execution_proof/live_execution_validation.json")
    gb = _read(ad, "execution_proof/gate_b_live_execution_validation.json")

    # Support evidence (Telegram) comes from runtime post_trade_hub manifest.
    post_trade_manifest = _read(ad, "state/post_trade_manifest.json")
    last_event = post_trade_manifest.get("last_event") if isinstance(post_trade_manifest.get("last_event"), dict) else {}
    telegram_ok = bool(last_event.get("telegram_sent")) if trade_id and str(last_event.get("trade_id") or "") == trade_id else False

    # Loop + rebuy truth
    loop = _read(ad, "data/control/universal_execution_loop_proof.json")
    rebuy = _read(ad, "data/control/rebuy_runtime_truth.json")

    # Lessons + CEO + review packet evidence (presence / freshness only).
    lessons = _read(ad, "data/control/lessons_runtime_truth.json")
    lessons_eff = _read(ad, "data/control/lessons_runtime_effect.json")
    ceo = _read(ad, "data/control/ceo_session_truth.json")

    review_packet_path = root / "shark" / "memory" / "global" / "review_packet_latest.json"
    review_packet_present = review_packet_path.is_file()

    exec_profile = str(lv.get("execution_profile") or "")
    proof = ga if exec_profile == "gate_a" else gb if exec_profile == "gate_b" else {}

    # Core booleans — evidence-first (from proof or cycle summaries).
    out = {
        "truth_version": _TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "last_cycle_ts": str(cycle.get("ts") or ""),
        "daemon_mode": str(cycle.get("mode") or ""),
        "avenue_a_daemon_product_mode": cycle.get("avenue_a_daemon_product_mode"),
        "lane": {
            "lane_chosen": lane.get("lane") or ("gate_b" if gate == "gate_b" else "gate_a" if gate == "gate_a" else None),
            "lane_reason": lane.get("why"),
            "lane_details": lane,
        },
        "cycle_outcome": {
            "ok": bool(cycle.get("ok")),
            "skipped": bool(cycle.get("skipped")),
            "skip_reason": cycle.get("skip_reason"),
            "skip_classification": cycle.get("skip_classification"),
            "blocked": cycle.get("blocked"),
        },
        "trade": {
            "trade_id": trade_id or None,
            "gate": gate or None,
            "execution_profile": exec_profile or None,
            "selected_product_source": lv.get("selected_product_source"),
        },
        "pipeline": {
            "execution_success": proof.get("execution_success"),
            "FINAL_EXECUTION_PROVEN": proof.get("FINAL_EXECUTION_PROVEN"),
            "coinbase_order_verified": proof.get("coinbase_order_verified"),
            "databank_written": proof.get("databank_written"),
            "supabase_synced": proof.get("supabase_synced"),
            "governance_logged": proof.get("governance_logged"),
            "packet_updated": proof.get("packet_updated"),
            "scheduler_stable": proof.get("scheduler_stable"),
        },
        "support_bots": {
            "telegram": {
                "evidence_path": "state/post_trade_manifest.json",
                "last_event": last_event,
                "telegram_ok_for_trade_id": telegram_ok,
                "honesty": "Telegram ok is true only when post_trade_manifest.last_event matches this trade_id and telegram_sent=true.",
            },
            "supabase": {
                "supabase_synced": proof.get("supabase_synced"),
                "supabase_sync_diagnostics": proof.get("supabase_sync_diagnostics"),
            },
            "databank": {"databank_written": proof.get("databank_written")},
        },
        "learning_and_review": {
            "lessons_runtime_truth_present": bool(lessons.get("truth_version")),
            "lessons_runtime_effect_present": bool(lessons_eff),
            "LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN": lessons_eff.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN"),
            "ceo_session_truth_present": bool(ceo.get("truth_version")),
            "review_packet_latest_present": review_packet_present,
            "review_packet_latest_path": str(review_packet_path),
        },
        "rebuy": {
            "rebuy_allowed_now": rebuy.get("rebuy_allowed_now"),
            "exact_reason_if_blocked": rebuy.get("exact_reason_if_blocked"),
            "universal_loop_proof": {
                "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": loop.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN"),
                "final_execution_proven": loop.get("final_execution_proven"),
                "last_trade_id": loop.get("last_trade_id"),
            },
        },
        "truth_sources": {
            "runtime_runner_last_cycle": "data/control/runtime_runner_last_cycle.json",
            "gate_a_proof": "execution_proof/live_execution_validation.json",
            "gate_b_proof": "execution_proof/gate_b_live_execution_validation.json",
            "post_trade_manifest": "state/post_trade_manifest.json",
            "rebuy_runtime_truth": "data/control/rebuy_runtime_truth.json",
        },
        "honesty": "This artifact summarizes last cycle from durable runtime artifacts; it does not infer success beyond proof booleans.",
    }
    return out


def write_avenue_a_coordination_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    payload = build_avenue_a_coordination_truth(runtime_root=root)
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_REL, payload)
    ad.write_text(_REL.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")
    return payload

