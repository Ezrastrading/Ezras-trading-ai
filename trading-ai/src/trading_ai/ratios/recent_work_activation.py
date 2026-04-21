"""Honest activation status for recent routing/capital/ratio work — no fake deployment claims."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.ratios.gap_closure import distinction_fields_reference


def _exists(p: Path) -> bool:
    return p.is_file()


def build_recent_work_activation_audit(*, runtime_root: Path) -> Dict[str, Any]:
    ctrl = runtime_root / "data" / "control"
    items: List[Dict[str, Any]] = [
        {
            "component": "validation_resolve coherent_v6",
            "module": "trading_ai.nte.execution.routing.integration.validation_resolve",
            "exists_in_repo": True,
            "covered_by_tests": True,
            "written_to_runtime_artifacts": "when EZRAS_WRITE_VALIDATION_CONTROL_ARTIFACTS or micro-validation pre-resolve",
            "invoked_by_validation_path": True,
            "invoked_by_readiness": "indirect via streak artifacts",
            "live_functional": "proven_in_tests_only unless Coinbase credentials + write flags",
            "status": "code_ready_not_deployed",
        },
        {
            "component": "deployable_capital_report + route_selection + portfolio_truth",
            "module": "trading_ai.nte.execution.routing.integration.capital_reports",
            "exists_in_repo": True,
            "covered_by_tests": True,
            "written_to_runtime_artifacts": str(ctrl / "deployable_capital_report.json"),
            "invoked_by_validation_path": True,
            "status": "runtime_artifact_proven when control writes run",
        },
        {
            "component": "universal_runtime_policy",
            "module": "trading_ai.nte.execution.routing.policy.universal_runtime_policy",
            "exists_in_repo": True,
            "invoked_by_validation_path": True,
            "status": "live_path_invoked via resolve_validation_product_coherent",
        },
        {
            "component": "universal_ratio_policy (this package)",
            "module": "trading_ai.ratios",
            "exists_in_repo": True,
            "invoked_by_validation_path": True,
            "note": "refresh_ratio_artifacts_after_validation runs after control artifact writes when enabled",
            "status": "validation_path_invoked_when_EZRAS_RATIO_REFRESH_ON_VALIDATION",
        },
        {
            "component": "ratio_policy_snapshot.json",
            "path": str(ctrl / "ratio_policy_snapshot.json"),
            "exists_in_repo": True,
            "artifact_file_committed_in_git": False,
            "note": "Generator lives in trading_ai.ratios; JSON is runtime output — not vendored as source truth in git.",
            "status": "not_yet_invoked until CLI or checklist",
        },
    ]
    for row in items:
        p = row.get("path")
        if isinstance(p, str) and p.endswith(".json"):
            row["artifact_exists_now"] = _exists(Path(p))
        df = distinction_fields_reference()
        df["code_exists"] = bool(row.get("exists_in_repo", True))
        df["imported"] = df["code_exists"]
        df["invoked"] = bool(row.get("invoked_by_validation_path"))
        df["artifact_written"] = bool(row.get("artifact_exists_now"))
        df["test_covered"] = bool(row.get("covered_by_tests"))
        df["local_runtime_proven"] = df["artifact_written"]
        row["distinction_fields"] = df
        row["next_status_if_external"] = (
            "runtime_artifact_proven"
            if not df["artifact_written"] and row.get("invoked_by_validation_path")
            else "keep_current_until_writer_runs"
        )

    return {
        "artifact": "recent_work_activation_audit",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root),
        "honesty_note": "deployed_ready only if your host/Railway applied this repo revision; not verifiable from code alone.",
        "distinction_fields_reference": distinction_fields_reference(),
        "items": items,
        "status_legend": {
            "code_ready_not_deployed": "Merged in repo; external env may not run it yet.",
            "wired_not_runtime_proven": "Importable; no proof file without running writer.",
            "proven_in_tests_only": "pytest covers logic; production needs credentials/flags.",
            "runtime_artifact_proven when control writes run": "Artifact appears after validation/micro-validation.",
            "not_yet_invoked until CLI or checklist": "Run ratio snapshot command.",
        },
    }


def write_recent_work_activation_audit(runtime_root: Path) -> Dict[str, str]:
    payload = build_recent_work_activation_audit(runtime_root=runtime_root)
    ctrl = runtime_root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    jp = ctrl / "recent_work_activation_audit.json"
    tp = ctrl / "recent_work_activation_audit.txt"
    jp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tp.write_text(json.dumps(payload, indent=2, default=str)[:24000] + "\n", encoding="utf-8")
    return {"recent_work_activation_audit_json": str(jp), "recent_work_activation_audit_txt": str(tp)}
