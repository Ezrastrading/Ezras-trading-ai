"""
Universal live-switch truth artifact — one envelope; per-avenue views use the same semantic strictness.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _read(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _avenue_view_coinbase(ctrl: Path, final: Dict[str, Any]) -> Dict[str, Any]:
    """A — Gate B truth is authoritative for Coinbase production switch; Gate A is separate snapshot."""
    ga = {}
    try:
        from trading_ai.deployment.gate_a_live_truth import gate_a_live_truth_snapshot

        ga = gate_a_live_truth_snapshot()
    except Exception as exc:
        ga = {"error": str(exc)}
    return {
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "can_switch_live_now": bool(final.get("gate_b_can_be_switched_live_now")),
        "if_false_exact_why": final.get("if_false_exact_why"),
        "manual_steps_remaining": final.get("manual_steps_remaining") or final.get("gate_b_operator_manual_steps_remaining"),
        "technical_blockers_remaining": final.get("technical_blockers_remaining"),
        "operator_ack_required": final.get("operator_ack_required"),
        "operator_ack_present": final.get("operator_ack_present"),
        "micro_proven": final.get("gate_b_live_micro_proven"),
        "live_order_ready": final.get("gate_b_ready_for_live_orders") or final.get("semantic_live_order_ready"),
        "repeated_tick_ready": final.get("repeated_tick_ready") or final.get("semantic_repeated_tick_ready"),
        "continuous_loop_ready": final.get("continuous_loop_ready_in_repo_daemon") or final.get("semantic_continuous_loop_ready_in_repo_daemon"),
        "lessons_runtime_intelligence_ready": final.get("lessons_runtime_intelligence_ready") or final.get("gate_b_lessons_runtime_active"),
        "gate_b_final_go_live_truth_path": str(ctrl / "gate_b_final_go_live_truth.json"),
        "gate_a_live_truth_snapshot": ga,
        "honesty": "Gate B readiness comes from gate_b_final_go_live_truth.json and related control artifacts — not from this universal envelope alone.",
    }


def _avenue_view_kalshi() -> Dict[str, Any]:
    return {
        "avenue_id": "B",
        "avenue_name": "kalshi",
        "can_switch_live_now": False,
        "if_false_exact_why": "universal_live_switch_for_avenue_B_not_yet_bound_to_structured_kalshi_live_proof_artifacts",
        "manual_steps_remaining": ["Wire Kalshi fills + PnL persistence into universal truth cycle and refresh artifacts."],
        "technical_blockers_remaining": ["kalshi_universal_adapter_not_wired"],
        "operator_ack_required": None,
        "operator_ack_present": None,
        "micro_proven": False,
        "live_order_ready": False,
        "repeated_tick_ready": False,
        "continuous_loop_ready": False,
        "lessons_runtime_intelligence_ready": False,
        "honesty": "Shark/Kalshi execution paths exist separately; universal live-switch parity is not claimed until wired.",
    }


def _avenue_view_tastytrade() -> Dict[str, Any]:
    return {
        "avenue_id": "C",
        "avenue_name": "tastytrade",
        "can_switch_live_now": False,
        "if_false_exact_why": "tastytrade_universal_live_switch_not_wired",
        "manual_steps_remaining": ["Implement Tastytrade fill truth + PnL under universal normalized record."],
        "technical_blockers_remaining": ["tastytrade_universal_adapter_not_wired"],
        "operator_ack_required": None,
        "operator_ack_present": None,
        "micro_proven": False,
        "live_order_ready": False,
        "repeated_tick_ready": False,
        "continuous_loop_ready": False,
        "lessons_runtime_intelligence_ready": False,
        "honesty": "No Tastytrade live proof is asserted by this repository layer yet.",
    }


def build_universal_live_switch_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    final = _read(ctrl / "gate_b_final_go_live_truth.json") or {}

    payload = {
        "truth_version": "universal_live_switch_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "can_switch_live_now": bool(final.get("gate_b_can_be_switched_live_now")),
        "if_false_exact_why": final.get("if_false_exact_why"),
        "universal_semantics": (
            "can_switch_live_now is True only when the authoritative Gate B artifact reports True for Coinbase; "
            "Avenue B/C are explicitly false until their universal proofs exist."
        ),
        "avenues": {
            "A": _avenue_view_coinbase(ctrl, final),
            "B": _avenue_view_kalshi(),
            "C": _avenue_view_tastytrade(),
        },
    }
    return payload


def write_universal_live_switch_truth_artifact(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    payload = build_universal_live_switch_truth(runtime_root=root)
    path = ctrl / "universal_live_switch_truth.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return {"path_json": str(path), "written": True, "payload": payload}
