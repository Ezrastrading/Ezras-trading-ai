"""
Single high-signal operator report: supervised vs autonomous vs gates vs infra (artifact-driven).

Does not enable trading; reads on-disk truth and check_env only.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.orchestration.autonomous_blocker_playbook import enrich_active_blockers_with_playbook
from trading_ai.deployment.check_env import run_check_env
from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


@contextmanager
def _with_runtime_env(runtime_root: Path):
    """Temporarily pin EZRAS_RUNTIME_ROOT for helpers that read ezras_runtime_root()."""
    root = str(Path(runtime_root).resolve())
    prev = os.environ.get("EZRAS_RUNTIME_ROOT")
    os.environ["EZRAS_RUNTIME_ROOT"] = root
    try:
        yield root
    finally:
        if prev is None:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)
        else:
            os.environ["EZRAS_RUNTIME_ROOT"] = prev


def build_controlled_live_readiness_report(*, runtime_root: Path, write_artifact: bool = True) -> Dict[str, Any]:
    """
    Answers: env/SSL/Coinbase, Gate A/B posture, Avenue A supervised + autonomous, Supabase schema file, proof alignment.
    """
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)

    with _with_runtime_env(root):
        env_structured = run_check_env()

    ap = build_autonomous_operator_path(runtime_root=root)
    playbook_rows = enrich_active_blockers_with_playbook(ap.get("active_blockers") or [])

    sup_truth = ad.read_json("data/control/avenue_a_supervised_live_truth.json") or {}
    daemon_enable = ad.read_json("data/control/daemon_enable_readiness_after_supervised.json") or {}
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}

    sup_blockers: List[str] = []
    if not bool(sup_truth.get("supervised_live_runtime_proven")):
        sup_blockers.append("supervised_live_runtime_not_proven")
    if not bool(daemon_enable.get("avenue_a_can_enable_daemon_now")):
        sup_blockers.extend([str(x) for x in (daemon_enable.get("exact_blockers") or [])[:20]])
    if not bool(auth.get("avenue_a_can_run_supervised_live_now")):
        sup_blockers.extend([str(x) for x in (auth.get("exact_blockers_supervised") or [])[:20]])

    sup_blockers = list(dict.fromkeys([b for b in sup_blockers if b]))

    gate_a_blockers: List[str] = []
    final_b = ad.read_json("data/control/avenue_a_final_live_blockers.json") or {}
    if isinstance(final_b.get("blockers"), list):
        gate_a_blockers.extend([str(x) for x in final_b["blockers"][:24]])
    if not bool(sup_truth.get("latest_gate_a_proof_strict_ok")):
        gate_a_blockers.append(
            f"latest_gate_a_proof_strict_not_ok:{sup_truth.get('latest_gate_a_proof_strict_reason') or 'unknown'}"
        )
    gate_a_blockers = list(dict.fromkeys(gate_a_blockers))

    gb_compact_path = root / "data" / "reports" / "gate_b_operator_readiness_compact.json"
    gb_snap_path = root / "data" / "control" / "gate_b_selection_snapshot.json"
    gb_live_path = root / "data" / "control" / "gate_b_live_status.json"
    gb_compact = _read_json(gb_compact_path) or {}
    gb_snap = _read_json(gb_snap_path) or {}
    gb_live = _read_json(gb_live_path) or {}

    gate_b_blockers: List[str] = []
    for k in ("gate_b_supervised_operator_blockers", "operator_blockers"):
        raw = gb_snap.get(k) if isinstance(gb_snap, dict) else None
        if isinstance(raw, list):
            gate_b_blockers.extend([str(x) for x in raw[:16]])
    if isinstance(gb_compact, dict):
        if gb_compact.get("deployable_for_gate_b_orders") is False:
            gate_b_blockers.append("gate_b_not_deployable_per_compact_readiness")
        for x in (gb_compact.get("blockers") or [])[:12]:
            gate_b_blockers.append(str(x))
    if isinstance(gb_live, dict) and gb_live.get("gate_b_ready_for_live") is False:
        gate_b_blockers.append("gate_b_ready_for_live_false_per_live_status")
    gate_b_blockers = list(dict.fromkeys([b for b in gate_b_blockers if b]))

    shared_infra: List[str] = []
    if not env_structured.get("coinbase_credentials_ok"):
        shared_infra.append("coinbase_credentials_missing_or_incomplete")
    ssl = env_structured.get("ssl_runtime") or {}
    if isinstance(ssl, dict) and ssl.get("ssl_guard_would_pass") is False:
        shared_infra.append("ssl_runtime_not_ok_python_openssl")

    dep_schema = root / "data" / "deployment" / "supabase_schema_readiness.json"
    schema = _read_json(dep_schema) or {}
    supabase_ok = bool(schema.get("schema_ready") is True)
    if not supabase_ok:
        shared_infra.append(
            f"supabase_schema_not_ready:{schema.get('error_classification') or schema.get('reason') or 'see_artifact'}"
        )

    bundle = ad.read_json("data/control/autonomous_verification_proof_bundle.json") or {}
    bundle_green = bool(bundle.get("all_runtime_components_verified"))
    ver_sum = ap.get("autonomous_verification_summary") or {}
    proof_alignment = {
        "autonomous_verification_proof_bundle_path": str(root / "data" / "control" / "autonomous_verification_proof_bundle.json"),
        "bundle_all_runtime_components_verified": bundle_green,
        "operator_path_reports_bundle_all_green": ver_sum.get("bundle_all_green"),
        "semantic_match": bundle_green == ver_sum.get("bundle_all_green"),
        "honesty": (
            "If semantic_match is false, re-run: python -m trading_ai.deployment autonomous-verification-smoke "
            "then avenue-a-go-live-verdict from the same shell."
        ),
    }

    can_a_autonomous_arm = bool(ap.get("can_arm_autonomous_now"))

    ordered_verify_commands = [
        "python -m trading_ai.deployment check-env",
        "python -m trading_ai.deployment controlled-live-readiness",
        "python -m trading_ai.deployment gate-a-selection-smoke",
        "python -m trading_ai.deployment gate-b-selection-smoke --deployable-usd 250",
        "python -m trading_ai.deployment coinbase-selection-report --deployable-usd 250",
        "python -m trading_ai.deployment avenue-a-daemon-status",
        "python -m trading_ai.deployment avenue-a-go-live-verdict",
        "python -m trading_ai.deployment autonomous-proof-report",
    ]

    def _src(blocker: str, category: str) -> Dict[str, str]:
        return {"blocker": blocker, "category": category, "source_artifacts": [], "proof_fields_missing": []}

    blocker_lineage = {
        "avenue_a_supervised": [_src(b, "avenue_a_supervised") for b in sup_blockers],
        "avenue_a_autonomous": [
            {**_src(str(b), "avenue_a_autonomous"), "source_artifacts": list((ap.get("current_authority_sources") or {}).values())[:12]}
            for b in (ap.get("active_blockers") or [])[:40]
            if b
        ],
        "gate_a": [_src(b, "gate_a") for b in gate_a_blockers],
        "gate_b": [_src(b, "gate_b") for b in gate_b_blockers],
        "shared_infra": [_src(b, "shared_infra") for b in shared_infra],
    }

    human_summary_lines = [
        "Controlled live readiness (conjunctive; advisory-only).",
        f"Runtime root: {root}",
        f"Avenue A supervised blockers ({len(sup_blockers)}): " + ("; ".join(sup_blockers) if sup_blockers else "none"),
        f"Avenue A autonomous blockers ({len(ap.get('active_blockers') or [])}): see JSON for deduped list.",
        f"Gate A blockers ({len(gate_a_blockers)}): " + ("; ".join(gate_a_blockers[:8]) if gate_a_blockers else "none"),
        f"Gate B blockers ({len(gate_b_blockers)}): " + ("; ".join(gate_b_blockers[:8]) if gate_b_blockers else "none"),
        f"Shared infra: " + ("; ".join(shared_infra) if shared_infra else "none"),
        "Historical notes are kept separate under avenue_a_autonomous.historical_notes_separate — never treated as active blockers.",
        "Next: run ordered_commands_to_verify from JSON.",
    ]
    human_summary = "\n".join(human_summary_lines)

    payload: Dict[str, Any] = {
        "truth_version": "controlled_live_readiness_v2",
        "runtime_root": str(root),
        "env_ssl_coinbase": {
            "coinbase_credentials_ok": env_structured.get("coinbase_credentials_ok"),
            "exact_missing_coinbase_env_vars": env_structured.get("exact_missing_coinbase_env_vars"),
            "ssl_runtime": env_structured.get("ssl_runtime"),
        },
        "avenue_a_supervised": {
            "can_run_supervised_live_now": bool(auth.get("avenue_a_can_run_supervised_live_now")),
            "supervised_live_runtime_proven": bool(sup_truth.get("supervised_live_runtime_proven")),
            "avenue_a_can_enable_daemon_now": bool(daemon_enable.get("avenue_a_can_enable_daemon_now")),
            "supervised_blockers_deduped": sup_blockers,
            "artifacts": {
                "avenue_a_supervised_live_truth": "data/control/avenue_a_supervised_live_truth.json",
                "daemon_enable_readiness_after_supervised": "data/control/daemon_enable_readiness_after_supervised.json",
            },
        },
        "avenue_a_autonomous": {
            "can_arm_autonomous_now": can_a_autonomous_arm,
            "can_submit_live_orders_under_dual_gate": bool(ap.get("can_submit_live_orders_under_dual_gate")),
            "active_blockers_deduped": ap.get("active_blockers"),
            "historical_notes_separate": ap.get("historical_notes"),
            "autonomous_blocker_playbook": playbook_rows,
            "artifacts": ap.get("current_authority_sources"),
        },
        "gate_a": {
            "gate_a_blockers_deduped": gate_a_blockers,
            "artifact": "data/control/avenue_a_final_live_blockers.json",
        },
        "gate_b": {
            "gate_b_blockers_deduped": gate_b_blockers,
            "artifacts": {
                "operator_readiness_compact": (
                    "data/reports/gate_b_operator_readiness_compact.json" if gb_compact_path.is_file() else None
                ),
                "selection_snapshot": "data/control/gate_b_selection_snapshot.json",
                "live_status": "data/control/gate_b_live_status.json",
            },
            "selection_summary_excerpt": (gb_snap.get("selection_summary") if isinstance(gb_snap, dict) else None),
        },
        "shared_infra_blockers_deduped": shared_infra,
        "supabase_persistence": {
            "schema_readiness_path": str(dep_schema),
            "schema_ready": supabase_ok,
            "error_classification": schema.get("error_classification"),
        },
        "proof_bundle_alignment": proof_alignment,
        "human_summary": human_summary,
        "blocker_lineage": blocker_lineage,
        "rollup_answers": {
            "is_avenue_a_supervised_live_ready": bool(auth.get("avenue_a_can_run_supervised_live_now"))
            and len(sup_blockers) == 0,
            "is_avenue_a_autonomous_arm_ready": can_a_autonomous_arm,
            "is_gate_a_ready_for_autonomous_once_arm_clear": len(gate_a_blockers) == 0,
            "is_gate_b_ready_for_supervised_live": len(gate_b_blockers) == 0
            and bool(gb_snap.get("gate_b_ready_for_supervised_use") is True),
            "is_gate_b_ready_for_autonomous_once_arm_clear": False,
            "is_supabase_schema_clean": supabase_ok,
            "are_env_ssl_coinbase_commands_clean": bool(env_structured.get("coinbase_credentials_ok")),
        },
        "ordered_commands_to_verify": ordered_verify_commands,
        "honesty": (
            "rollup_answers are conservative booleans from artifacts on disk; "
            "Gate B autonomous-ready is always false here — requires governance + dual gate beyond this report."
        ),
    }
    if write_artifact:
        try:
            outp = root / "data" / "control" / "controlled_live_readiness.json"
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
            payload["written_artifact_path"] = str(outp)
            summary_path = root / "data" / "control" / "controlled_live_readiness_summary.txt"
            summary_path.write_text(human_summary + "\n", encoding="utf-8")
            payload["written_summary_path"] = str(summary_path)
        except OSError:
            payload["written_artifact_path"] = None
            payload["written_summary_path"] = None
    return payload
