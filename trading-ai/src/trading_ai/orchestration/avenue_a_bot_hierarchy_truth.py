"""
Evidence-first Avenue A bot hierarchy truth.

This module does NOT grant permissions, bypass safety, or claim activity from code presence.
It classifies bots as:
  - active: runtime evidence contract satisfied
  - advisory_only: code exists and is referenced, but runtime evidence contract not satisfied
  - dead: explicitly unwired / no evidence path and not referenced by current Avenue A loop
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_TRUTH_VERSION = "avenue_a_bot_hierarchy_truth_v1"
_REL = "data/control/avenue_a_bot_hierarchy_truth.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(ad: LocalStorageAdapter, rel: str) -> Dict[str, Any]:
    try:
        j = ad.read_json(rel)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _age_sec(path: Path) -> Optional[float]:
    try:
        if not path.is_file():
            return None
        return max(0.0, (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))
    except Exception:
        return None


def _fresh_enough(age_sec: Optional[float], *, max_age_sec: float) -> bool:
    return age_sec is not None and age_sec <= max_age_sec


def _bot(
    *,
    bot_id: str,
    layer: str,
    role: str,
    parent_bot_id: Optional[str],
    gate_id: Optional[str],
    evidence: Dict[str, Any],
    state: str,
    state_reason: str,
) -> Dict[str, Any]:
    return {
        "bot_id": bot_id,
        "layer": layer,
        "role": role,
        "parent_bot_id": parent_bot_id,
        "avenue_id": "A",
        "gate_id": gate_id,
        "state": state,
        "state_reason": state_reason,
        "evidence": evidence,
    }


def _classify_from_bool(*, ok: Optional[bool], when_ok: str, when_not: str, evidence: Dict[str, Any]) -> Tuple[str, str]:
    if ok is True:
        return "active", when_ok
    if ok is False:
        return "advisory_only", when_not
    return "advisory_only", "evidence_unavailable:" + when_not


def build_avenue_a_bot_hierarchy_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = LocalStorageAdapter(runtime_root=root)

    active_stack = _read_json(ad, "data/control/avenue_a_active_stack_truth.json")
    daemon_truth = _read_json(ad, "data/control/avenue_a_daemon_live_truth.json")
    last_cycle = _read_json(ad, "data/control/runtime_runner_last_cycle.json").get("avenue_a_daemon") or {}
    if not isinstance(last_cycle, dict):
        last_cycle = {}
    lv = last_cycle.get("live_validation") or {}
    lv = lv if isinstance(lv, dict) else {}

    gate_a_proof_path = root / "execution_proof" / "live_execution_validation.json"
    gate_b_proof_path = root / "execution_proof" / "gate_b_live_execution_validation.json"
    ga = _read_json(ad, "execution_proof/live_execution_validation.json")
    gb = _read_json(ad, "execution_proof/gate_b_live_execution_validation.json")

    # Telegram runtime evidence comes from post_trade_hub manifest under runtime root (not monorepo logs).
    post_trade_manifest = _read_json(ad, "state/post_trade_manifest.json")
    last_event = post_trade_manifest.get("last_event") if isinstance(post_trade_manifest.get("last_event"), dict) else {}

    lessons_runtime_truth = _read_json(ad, "data/control/lessons_runtime_truth.json")
    lessons_effect = _read_json(ad, "data/control/lessons_runtime_effect.json")
    ceo = _read_json(ad, "data/control/ceo_session_truth.json")

    review_packet_path = root / "shark" / "memory" / "global" / "review_packet_latest.json"
    review_age = _age_sec(review_packet_path)

    mode = str((last_cycle.get("mode") or daemon_truth.get("daemon_mode") or os.environ.get("EZRAS_AVENUE_A_DAEMON_MODE") or "")).strip()
    daemon_truth_age = _age_sec(root / "data" / "control" / "avenue_a_daemon_live_truth.json")

    # Evidence window: last 24h for "freshness" oriented support bots.
    max_support_age = 24 * 3600.0

    bots: List[Dict[str, Any]] = []

    # Layer 1 — Avenue master
    master_id = "avenue_a_master"
    master_ok = bool(mode) and mode != "disabled" and _fresh_enough(daemon_truth_age, max_age_sec=max_support_age)
    master_state, master_reason = _classify_from_bool(
        ok=master_ok,
        when_ok="daemon_truth_fresh_and_mode_enabled",
        when_not="daemon_truth_missing_or_stale_or_mode_disabled",
        evidence={"daemon_mode": mode, "daemon_truth_age_sec": daemon_truth_age, "truth_source": "data/control/avenue_a_daemon_live_truth.json"},
    )
    bots.append(
        _bot(
            bot_id=master_id,
            layer="L1_AVENUE_MASTER",
            role="Choose lane; coordinate cycle state; avenue-level truth/readiness; rebuy continuity.",
            parent_bot_id=None,
            gate_id=None,
            evidence={"daemon_mode": mode, "daemon_truth_age_sec": daemon_truth_age},
            state=master_state,
            state_reason=master_reason,
        )
    )

    # Layer 2 — Gate managers
    gm_a_ok = bool(active_stack.get("gate_a_manager_active"))
    gm_a_state, gm_a_reason = _classify_from_bool(
        ok=gm_a_ok,
        when_ok="active_stack_gate_a_manager_active_true",
        when_not="active_stack_gate_a_manager_active_false_or_missing",
        evidence={"truth_source": "data/control/avenue_a_active_stack_truth.json:gate_a_manager_active"},
    )
    bots.append(
        _bot(
            bot_id="gate_a_manager",
            layer="L2_GATE_MANAGER",
            role="Own Gate A lifecycle; selection+execution contract; gate-specific readiness/proofs.",
            parent_bot_id=master_id,
            gate_id="gate_a",
            evidence={"active_stack_gate_a_manager_active": active_stack.get("gate_a_manager_active")},
            state=gm_a_state,
            state_reason=gm_a_reason,
        )
    )

    gm_b_ok = bool(active_stack.get("gate_b_manager_active"))
    gm_b_state, gm_b_reason = _classify_from_bool(
        ok=gm_b_ok,
        when_ok="active_stack_gate_b_manager_active_true",
        when_not="active_stack_gate_b_manager_active_false_or_missing",
        evidence={"truth_source": "data/control/avenue_a_active_stack_truth.json:gate_b_manager_active"},
    )
    bots.append(
        _bot(
            bot_id="gate_b_manager",
            layer="L2_GATE_MANAGER",
            role="Own Gate B lifecycle; scanner/selection+execution contract; gate-specific readiness/proofs.",
            parent_bot_id=master_id,
            gate_id="gate_b",
            evidence={"active_stack_gate_b_manager_active": active_stack.get("gate_b_manager_active")},
            state=gm_b_state,
            state_reason=gm_b_reason,
        )
    )

    # Layer 3 — Execution / selection bots (ACTIVE only when observed in last daemon cycle)
    exec_profile = str(lv.get("execution_profile") or "")
    sel_src = str(lv.get("selected_product_source") or "")

    bots.append(
        _bot(
            bot_id="gate_a_selection_engine",
            layer="L3_EXECUTION_SELECTION",
            role="Gate A product selection snapshot + majors lane policy.",
            parent_bot_id="gate_a_manager",
            gate_id="gate_a",
            evidence={"last_execution_profile": exec_profile, "last_selected_product_source": sel_src, "truth_source": "runtime_runner_last_cycle.json"},
            state="active" if (exec_profile == "gate_a" and sel_src == "gate_a_selection_engine") else "advisory_only",
            state_reason="active_only_when_last_cycle_used_gate_a_selection_engine",
        )
    )
    bots.append(
        _bot(
            bot_id="gate_b_gainers_selection_engine",
            layer="L3_EXECUTION_SELECTION",
            role="Gate B gainers/scanner selection snapshot.",
            parent_bot_id="gate_b_manager",
            gate_id="gate_b",
            evidence={"last_execution_profile": exec_profile, "last_selected_product_source": sel_src, "truth_source": "runtime_runner_last_cycle.json"},
            state="active" if (exec_profile == "gate_b" and sel_src == "gate_b_gainers_selection_engine") else "advisory_only",
            state_reason="active_only_when_last_cycle_used_gate_b_gainers_selection_engine",
        )
    )

    ga_ok = bool(ga.get("FINAL_EXECUTION_PROVEN") and ga.get("execution_success"))
    bots.append(
        _bot(
            bot_id="gate_a_execution_validation",
            layer="L3_EXECUTION_SELECTION",
            role="Gate A live round-trip proof + pipeline booleans.",
            parent_bot_id="gate_a_manager",
            gate_id="gate_a",
            evidence={"proof_path": "execution_proof/live_execution_validation.json", "FINAL_EXECUTION_PROVEN": ga.get("FINAL_EXECUTION_PROVEN")},
            state="active" if ga_ok else "advisory_only",
            state_reason="active_only_when_gate_a_proof_FINAL_EXECUTION_PROVEN_true",
        )
    )

    gb_ok = bool(gb.get("FINAL_EXECUTION_PROVEN") and gb.get("execution_success"))
    bots.append(
        _bot(
            bot_id="gate_b_execution_validation",
            layer="L3_EXECUTION_SELECTION",
            role="Gate B live-micro round-trip proof + pipeline booleans.",
            parent_bot_id="gate_b_manager",
            gate_id="gate_b",
            evidence={"proof_path": "execution_proof/gate_b_live_execution_validation.json", "FINAL_EXECUTION_PROVEN": gb.get("FINAL_EXECUTION_PROVEN")},
            state="active" if gb_ok else "advisory_only",
            state_reason="active_only_when_gate_b_proof_FINAL_EXECUTION_PROVEN_true",
        )
    )

    # Exit/rebuy managers exist logically but do not have separate proof contracts today.
    for bid, gate in (
        ("gate_a_exit_manager", "gate_a"),
        ("gate_a_rebuy_manager", "gate_a"),
        ("gate_b_exit_manager", "gate_b"),
        ("gate_b_rebuy_manager", "gate_b"),
    ):
        bots.append(
            _bot(
                bot_id=bid,
                layer="L3_EXECUTION_SELECTION",
                role="Gate-scoped exit/rebuy semantics (currently enforced via universal loop proof + rebuy_policy).",
                parent_bot_id="gate_a_manager" if gate == "gate_a" else "gate_b_manager",
                gate_id=gate,
                evidence={"truth_source": "data/control/universal_execution_loop_proof.json + data/control/rebuy_runtime_truth.json"},
                state="advisory_only",
                state_reason="no_separate_gate_exit_rebuy_proof_contract_present",
            )
        )

    # Layer 4 — Support / intelligence bots (ACTIVE only when last-cycle or recent artifacts prove)
    bots.append(
        _bot(
            bot_id="databank_writer",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Local trade databank write on completed trade.",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={
                "gate_a_databank_written": ga.get("databank_written"),
                "gate_b_databank_written": gb.get("databank_written"),
            },
            state="active" if bool(ga.get("databank_written") or gb.get("databank_written")) else "advisory_only",
            state_reason="active_only_when_latest_proof_databank_written_true",
        )
    )
    bots.append(
        _bot(
            bot_id="supabase_sync",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Supabase persistence on completed trade.",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={
                "gate_a_supabase_synced": ga.get("supabase_synced"),
                "gate_b_supabase_synced": gb.get("supabase_synced"),
            },
            state="active" if bool(ga.get("supabase_synced") or gb.get("supabase_synced")) else "advisory_only",
            state_reason="active_only_when_latest_proof_supabase_synced_true",
        )
    )

    tg_sent = bool(last_event.get("telegram_sent"))
    tg_age = _age_sec(root / "state" / "post_trade_manifest.json")
    bots.append(
        _bot(
            bot_id="telegram_alerts",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Telegram alerts (placed/closed) with idempotency.",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={
                "manifest_path": "state/post_trade_manifest.json",
                "manifest_age_sec": tg_age,
                "last_event": last_event,
            },
            state="active" if (tg_sent and _fresh_enough(tg_age, max_age_sec=max_support_age)) else "advisory_only",
            state_reason="active_only_when_post_trade_manifest_last_event_telegram_sent_true_and_fresh",
        )
    )

    # Lessons/progression/mission/review are evidence via their own truth artifacts.
    les_age = _age_sec(root / "data" / "control" / "lessons_runtime_truth.json")
    les_ok = bool(lessons_runtime_truth.get("truth_version")) and _fresh_enough(les_age, max_age_sec=max_support_age)
    bots.append(
        _bot(
            bot_id="lessons_engine",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Lessons store + runtime influence truth (evidence-first).",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={
                "lessons_runtime_truth_present": bool(lessons_runtime_truth.get("truth_version")),
                "lessons_runtime_truth_age_sec": les_age,
                "lessons_runtime_effect_present": bool(lessons_effect),
            },
            state="active" if les_ok else "advisory_only",
            state_reason="active_only_when_lessons_runtime_truth_present_and_fresh",
        )
    )
    bots.append(
        _bot(
            bot_id="lesson_runtime_influence",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Proof of whether lessons touched Gate B decisions.",
            parent_bot_id="lessons_engine",
            gate_id="gate_b",
            evidence={"lessons_runtime_effect": lessons_effect},
            state="active" if bool(lessons_effect.get("LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN")) else "advisory_only",
            state_reason="active_only_when_LESSONS_RUNTIME_DECISION_INFLUENCE_PROVEN_true",
        )
    )

    ceo_age = _age_sec(root / "data" / "control" / "ceo_session_truth.json")
    ceo_ok = bool(ceo.get("truth_version")) and _fresh_enough(ceo_age, max_age_sec=max_support_age)
    bots.append(
        _bot(
            bot_id="ceo_orchestration",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="CEO session freshness + orchestration inputs (evidence-first).",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={"ceo_session_truth_present": bool(ceo.get("truth_version")), "ceo_session_truth_age_sec": ceo_age},
            state="active" if ceo_ok else "advisory_only",
            state_reason="active_only_when_ceo_session_truth_present_and_fresh",
        )
    )

    bots.append(
        _bot(
            bot_id="runtime_refresh_manager",
            layer="L4_SUPPORT_INTELLIGENCE",
            role="Refresh runtime truth bundle (registry-driven).",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={"truth_source": "reports/runtime_artifact_registry.py + runtime_artifact_refresh_manager"},
            state="advisory_only",
            state_reason="refresh_manager_is_scheduler_driven_no_single_active_proof_contract",
        )
    )

    # Layer 5 — Morning review / control bots
    bots.append(
        _bot(
            bot_id="morning_review_summary_engine",
            layer="L5_MORNING_REVIEW_CONTROL",
            role="Morning review readiness from evidence artifacts (review packet + lessons + last trades).",
            parent_bot_id=master_id,
            gate_id=None,
            evidence={"review_packet_path": str(review_packet_path), "review_packet_age_sec": review_age},
            state="active" if _fresh_enough(review_age, max_age_sec=max_support_age) else "advisory_only",
            state_reason="active_only_when_review_packet_latest_present_and_fresh",
        )
    )
    bots.append(
        _bot(
            bot_id="daily_performance_summary_engine",
            layer="L5_MORNING_REVIEW_CONTROL",
            role="Daily performance snapshot (evidence via review artifacts).",
            parent_bot_id="morning_review_summary_engine",
            gate_id=None,
            evidence={"truth_source": "data/review/* + review_packet_latest.json"},
            state="advisory_only",
            state_reason="no_single_daily_performance_truth_contract_in_avenue_a_loop",
        )
    )
    bots.append(
        _bot(
            bot_id="autonomous_health_summary_engine",
            layer="L5_MORNING_REVIEW_CONTROL",
            role="Autonomous health summary (evidence via active_stack_truth + daemon truth).",
            parent_bot_id="morning_review_summary_engine",
            gate_id=None,
            evidence={"active_stack_present": bool(active_stack.get("truth_version")), "daemon_truth_present": bool(daemon_truth.get("truth_version"))},
            state="active" if bool(active_stack.get("truth_version") and daemon_truth.get("truth_version")) else "advisory_only",
            state_reason="active_only_when_active_stack_and_daemon_truth_present",
        )
    )
    bots.append(
        _bot(
            bot_id="bot_coordination_summary_engine",
            layer="L5_MORNING_REVIEW_CONTROL",
            role="Coordination summary (evidence via avenue_a_coordination_truth.json).",
            parent_bot_id="morning_review_summary_engine",
            gate_id=None,
            evidence={"truth_source": "data/control/avenue_a_coordination_truth.json"},
            state="advisory_only",
            state_reason="coordination_truth_writer_added_separately",
        )
    )

    # Missing explicit bot layers requested by operator: define them now as advisory-only unless evidence contract exists.
    # These IDs are stable (contract), but activity is evidence-first.
    def _advisory_child(bot_id: str, *, parent: str, gate: Optional[str], role: str, truth_source: str) -> None:
        bots.append(
            _bot(
                bot_id=bot_id,
                layer="L4_SUPPORT_INTELLIGENCE",
                role=role,
                parent_bot_id=parent,
                gate_id=gate,
                evidence={"truth_source": truth_source},
                state="advisory_only",
                state_reason="explicit_contract_defined_no_runtime_evidence_contract_yet",
            )
        )

    # Gate A missing bots
    for bid, role in (
        ("gate_a_scanner_bot", "Gate A scan/universe rows (majors lane)."),
        ("gate_a_risk_bot", "Gate A risk avoidance signals (failsafe/adaptive/governance context)."),
        ("gate_a_strategy_bot", "Gate A strategy selection/parameters (evidence via selection policy + proof)."),
        ("gate_a_profit_progression_bot", "Gate A profit progression tracking."),
        ("gate_a_goal_progression_bot", "Gate A goal progression tracking."),
        ("gate_a_rebuy_decision_bot", "Gate A rebuy decision policy (evidence via rebuy_runtime_truth)."),
    ):
        _advisory_child(bid, parent="gate_a_manager", gate="gate_a", role=role, truth_source="data/control/* + execution_proof/live_execution_validation.json")

    # Gate B missing bots
    for bid, role in (
        ("gate_b_scanner_bot", "Gate B scan/gainers candidate extraction."),
        ("gate_b_risk_bot", "Gate B risk avoidance signals (failsafe/adaptive/governance context)."),
        ("gate_b_strategy_bot", "Gate B strategy selection/parameters (gainers policy/tuning)."),
        ("gate_b_profit_progression_bot", "Gate B profit progression tracking."),
        ("gate_b_goal_progression_bot", "Gate B goal progression tracking."),
        ("gate_b_rebuy_decision_bot", "Gate B rebuy decision policy (evidence via rebuy_runtime_truth)."),
    ):
        _advisory_child(bid, parent="gate_b_manager", gate="gate_b", role=role, truth_source="data/control/gate_b_* + execution_proof/gate_b_live_execution_validation.json")

    # Shared missing bots
    for bid, role, src in (
        ("avenue_a_research_bot", "Research/edge notes pipeline.", "intelligence/edge_research/* + data/review/*"),
        ("avenue_a_opportunity_ranking_bot", "Opportunity ranking across Gate A vs Gate B.", "gate_a_selection_snapshot.json + gate_b_selection_snapshot.json"),
        ("avenue_a_capital_allocation_bot", "Capital governor / gate split advisory.", "coinbase_capital_split + capital_governor"),
        ("avenue_a_alerting_bot", "Alerting hub (telegram/post_trade_hub).", "state/post_trade_manifest.json + logs/post_trade_log.md"),
        ("avenue_a_review_bot", "Review prep pipeline (review_packet_latest).", "shark/memory/global/review_packet_latest.json"),
    ):
        _advisory_child(bid, parent=master_id, gate=None, role=role, truth_source=src)

    counts = {
        "active": sum(1 for b in bots if b.get("state") == "active"),
        "advisory_only": sum(1 for b in bots if b.get("state") == "advisory_only"),
        "dead": sum(1 for b in bots if b.get("state") == "dead"),
    }

    return {
        "truth_version": _TRUTH_VERSION,
        "generated_at": _iso(),
        "runtime_root": str(root),
        "mode": mode,
        "counts": counts,
        "bots": bots,
        "honesty": "States are evidence-first; advisory_only means code/contract exists but runtime evidence contract not satisfied.",
    }


def write_avenue_a_bot_hierarchy_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    payload = build_avenue_a_bot_hierarchy_truth(runtime_root=root)
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_REL, payload)
    ad.write_text(_REL.replace(".json", ".txt"), __import__("json").dumps(payload, indent=2, default=str) + "\n")
    return payload

