"""Write avenue orchestration truth, activation matrix, execution loop truth, A activation/blockers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trading_ai.multi_avenue.avenue_registry import build_avenue_registry_snapshot
from trading_ai.orchestration.avenue_contracts import merged_capabilities
from trading_ai.orchestration.execution_loop import full_chain_wired_report
from trading_ai.orchestration.lessons_truth import build_lessons_influence_truth
from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _write_txt(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _avenue_row(aid: str, *, runtime_root: Path) -> Dict[str, Any]:
    sw, blockers, diag = compute_avenue_switch_live_now(aid, runtime_root=runtime_root)
    caps = merged_capabilities(runtime_root=runtime_root).get(aid)
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    gng = ad.read_json("data/control/go_no_go_decision.json") or {}
    micro_ok = bool(gng.get("ready_for_micro_validation"))
    return {
        "avenue_id": aid,
        "code_ready": True,
        "tick_ready": getattr(caps, "can_tick", False) if caps else False,
        "live_micro_proven": micro_ok if aid == "A" else False,
        "live_order_ready": sw,
        "repeated_tick_ready": getattr(caps, "can_scan", False) if caps else False,
        "autonomous_daemon_ready": False,
        "switch_live_allowed_now": sw,
        "blockers": blockers,
        "next_command": (
            "python -m trading_ai.orchestration.orchestration_truth"
            if aid == "A"
            else "independent proof + enable gates + operator confirm"
        ),
        "lessons_influence_runtime": build_lessons_influence_truth(runtime_root=runtime_root),
        "global_halt_authoritative": True,
        "diagnostics": diag,
    }


def write_avenue_orchestration_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    rows = [_avenue_row("A", runtime_root=root), _avenue_row("B", runtime_root=root), _avenue_row("C", runtime_root=root)]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "avenues": rows,
        "honesty": "autonomous_daemon_ready defaults false until runner + live path proven in deployment.",
        "registry": build_avenue_registry_snapshot(runtime_root=root),
    }
    ad.write_json("data/control/avenue_orchestration_truth.json", payload)
    _write_txt(root / "data/control/avenue_orchestration_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_activation_matrix(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    mat = []
    for aid in ("A", "B", "C"):
        sw, blockers, _ = compute_avenue_switch_live_now(aid, runtime_root=root)
        mat.append({"avenue_id": aid, "switch_live_allowed": sw, "blockers": blockers})
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "matrix": mat}
    ad.write_json("data/control/avenue_activation_matrix.json", payload)
    _write_txt(root / "data/control/avenue_activation_matrix.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_execution_loop_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    chains = {a: full_chain_wired_report(avenue_id=a) for a in ("A", "B", "C")}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buy_sell_log_sync_rebuy_wired": {
            "question_1_chain_wired_in_code": True,
            "question_2_code_vs_runtime": "stages_are_code_addressable_runtime_proven_only_after_supervised_live",
            "question_3_avenue_support": chains,
            "question_4_blockers": {
                "A": "live_requires_operator_confirmation_and_locks",
                "B": "independent_live_proof_and_gate_b",
                "C": "scaffold_only",
            },
            "question_5_rebuy_requires_logging": True,
            "question_6_rebuy_requires_adaptive_and_governance": True,
        },
        "honesty": "Full buy→sell→rebuy runtime proof is not claimed here — only contract + partial integrations.",
    }
    ad.write_json("data/control/execution_loop_truth.json", payload)
    _write_txt(root / "data/control/execution_loop_truth.txt", json.dumps(payload, indent=2) + "\n")
    return payload


def write_avenue_a_activation_artifacts(*, runtime_root: Optional[Path] = None) -> None:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ok, blockers, diag = compute_avenue_switch_live_now("A", runtime_root=root)
    if ok:
        seq = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "steps": [
                "Confirm EZRAS_RUNTIME_ROOT on the host",
                "Set data/control/operator_live_confirmation.json confirmed true OR EZRAS_OPERATOR_LIVE_CONFIRMED=1",
                "Verify system_execution_lock.json: system_locked true, ready_for_live_execution true, gate_a enabled",
                "Clear system_kill_switch; verify failsafe not halted",
                "Run supervised micro-validation then smallest live notional",
            ],
            "diagnostics": diag,
        }
        ad.write_json("data/control/avenue_a_safe_activation_sequence.json", seq)
        _write_txt(
            root / "data/control/avenue_a_safe_activation_sequence.txt",
            json.dumps(seq, indent=2) + "\n",
        )
    else:
        blk = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blockers": blockers,
            "diagnostics": diag,
        }
        ad.write_json("data/control/avenue_a_activation_blockers.json", blk)
        _write_txt(root / "data/control/avenue_a_activation_blockers.txt", json.dumps(blk, indent=2) + "\n")


def write_all_orchestration_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    write_avenue_orchestration_truth(runtime_root=root)
    write_activation_matrix(runtime_root=root)
    write_execution_loop_truth(runtime_root=root)
    write_avenue_a_activation_artifacts(runtime_root=root)
    try:
        from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

        closure = write_live_switch_closure_bundle(
            runtime_root=root,
            trigger_surface="avenue_orchestration_truth",
            reason="orchestration_write_all",
        )
    except Exception as exc:
        closure = {"error": str(exc)}
    return {"ok": True, "runtime_root": str(root), "live_switch_closure": closure}
