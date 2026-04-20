"""Supervised readiness closer — end-to-end checklist from existing proofs (no live enable)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import trading_ai

from trading_ai.deployment.check_env import run_check_env
from trading_ai.deployment.controlled_live_readiness import build_controlled_live_readiness_report
from trading_ai.org_organism.io_utils import read_json_dict, write_json_atomic
from trading_ai.org_organism.paths import supervised_readiness_closer_path, supervised_sequence_plan_path
from trading_ai.runtime_checks.ssl_guard import ssl_runtime_diagnostic
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_path(path: Path) -> bool:
    return path.is_file()


def build_supervised_readiness_closer(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)

    env_lines = run_check_env()
    ssl_info = ssl_runtime_diagnostic()
    controlled = build_controlled_live_readiness_report(runtime_root=root, write_artifact=True)

    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    sup_truth = ad.read_json("data/control/avenue_a_supervised_live_truth.json") or {}
    refresh = read_json_dict(root / "data" / "control" / "runtime_artifact_refresh_truth.json")
    final_r = read_json_dict(root / "data" / "deployment" / "final_readiness.json")

    gate_sel = read_json_dict(root / "data" / "control" / "gate_a_selection_snapshot.json")
    gb_sel = read_json_dict(root / "data" / "control" / "gate_b_selection_snapshot.json")

    gov_proof = read_json_dict(root / "data" / "deployment" / "governance_proof.json")

    checklist: Dict[str, Any] = {
        "venue_credentials": bool(env_lines.get("coinbase_credentials_ok")),
        "ssl_runtime_correctness": bool((ssl_info or {}).get("ssl_guard_would_pass")),
        "environment_consistency": bool((ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}).get("consistent_with_authoritative_artifacts") is not False),
        "truth_chain_freshness": bool(refresh.get("refresh_complete_and_trustworthy")) if isinstance(refresh, dict) else False,
        "proof_artifact_freshness": bool(sup_truth.get("supervised_live_runtime_proven")),
        "gate_selection_readiness": bool(gate_sel and gate_sel.get("selected_product")),
        "gate_specific_config_present": bool(gb_sel),
        "supabase_sync_path": bool((controlled.get("supabase_persistence") or {}).get("schema_ready")),
        "databank_write_path": bool((controlled.get("supabase_persistence") or {}).get("schema_ready")),
        "governance_logging": isinstance(gov_proof, dict),
        "post_trade_hook_path": _bool_path(Path(trading_ai.__file__).resolve().parent / "automation" / "post_trade_cli.py"),
        "operator_confirmation_path": bool(auth.get("avenue_a_can_run_supervised_live_now") is not None),
    }

    blockers: List[str] = []
    if not checklist["venue_credentials"]:
        blockers.append("coinbase_credentials_incomplete")
    if not checklist["ssl_runtime_correctness"]:
        blockers.append("ssl_runtime_failure")
    if not checklist["proof_artifact_freshness"]:
        blockers.append("supervised_live_runtime_not_proven")
    if not checklist["supabase_sync_path"]:
        blockers.append("supabase_schema_not_ready")
    ra = (controlled.get("rollup_answers") or {}) if isinstance(controlled.get("rollup_answers"), dict) else {}
    if not ra.get("is_avenue_a_supervised_live_ready"):
        blockers.append("avenue_a_supervised_live_not_ready_per_rollup")

    cmd_seq = [
        "export EZRAS_RUNTIME_ROOT=" + str(root),
        "python -m trading_ai.deployment check-env",
        "python -m trading_ai.deployment refresh-runtime-artifacts",
        "python -m trading_ai.deployment controlled-live-readiness",
        "python -m trading_ai.deployment write-supervised-live-truth",
        "python -m trading_ai.deployment avenue-a-daemon-once --quote-usd 10 --product-id BTC-USD",
    ]

    ready = len(blockers) == 0 and bool(ra.get("is_avenue_a_supervised_live_ready"))

    out = {
        "truth_version": "supervised_readiness_closer_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "does_not_enable_autonomous_live": True,
        "ready_for_next_supervised_sequence": ready,
        "checklist": checklist,
        "exact_blockers": blockers,
        "exact_command_sequence": cmd_seq,
        "supporting_artifacts": {
            "controlled_live_readiness": "data/control/controlled_live_readiness.json",
            "final_readiness": "data/deployment/final_readiness.json",
            "daemon_live_switch_authority": "data/control/daemon_live_switch_authority.json",
        },
        "final_readiness_excerpt": {"exists": final_r is not None, "ready_for_first_20": (final_r or {}).get("ready_for_first_20")},
        "honesty": "Readiness is conjunctive with conservative rollup_answers; absence of proof is absence of readiness.",
    }
    write_json_atomic(supervised_readiness_closer_path(root), out)
    return out


def build_supervised_sequence_plan(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    closer = build_supervised_readiness_closer(runtime_root=root)
    ready = bool(closer.get("ready_for_next_supervised_sequence"))
    blockers_n = len(closer.get("exact_blockers") or [])

    if not ready or blockers_n > 0:
        rec_count = 0
        cadence = "do_not_run_until_blockers_cleared"
        note = "No disciplined micro-sequence while blockers remain — fix checklist items first."
    else:
        rec_count = 3
        cadence = "one_round_trip_per_session_max_until_streak_green"
        note = "Small quote (e.g. 10 USD), operator-attended, stop on first proof failure."

    plan = {
        "truth_version": "supervised_sequence_plan_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "no_large_exposure_recommended": True,
        "recommended_trade_count_initial": rec_count,
        "recommended_cadence": cadence,
        "operator_note": note,
        "blockers": closer.get("exact_blockers"),
        "commands_before_sequence": closer.get("exact_command_sequence"),
    }
    write_json_atomic(supervised_sequence_plan_path(root), plan)
    return plan
