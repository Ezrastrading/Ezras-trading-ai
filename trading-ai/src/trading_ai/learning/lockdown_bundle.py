"""
Operator dashboards and lockdown truth artifacts — honest classifications, no fake readiness.
All paths stay under EZRAS_RUNTIME_ROOT.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.learning.authority_model import load_authority_state, save_authority_state
from trading_ai.learning.performance_tracker import refresh_ai_performance_tracker
from trading_ai.learning.research_triggering import write_daily_research_review
from trading_ai.runtime_paths import ezras_runtime_root


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def _write_txt(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_gate_activation_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    gb = {}
    try:
        from trading_ai.shark.coinbase_spot.gate_b_live_status import gate_b_live_status_report

        gb = gate_b_live_status_report()
    except Exception as exc:
        gb = {"error": str(exc)}

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "avenue_a_gate_a": {
            "role": "Coinbase NTE / spot micro-validation and first-20 scope",
            "live_execution_path": "code_ready_runtime_gated_by_governance_and_policy",
            "readiness_note": "Gate A readiness (first-20) does not enable Gate B.",
        },
        "avenue_a_gate_b": {
            "gate_b_live_execution_enabled": gb.get("gate_b_live_execution_enabled"),
            "gate_b_production_state": gb.get("gate_b_production_state"),
            "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
            "meaning": gb.get("gate_b_ready_for_live_meaning"),
            "required_to_advance": (
                "Operator sets GATE_B_LIVE_EXECUTION_ENABLED and supplies data/control/gate_b_validation.json "
                "with validated_at for production-class Gate B."
            ),
        },
        "classification": "truthful_state_not_a_go_live_promise",
    }
    _write_json(root / "data" / "control" / "gate_activation_truth.json", payload)
    _write_txt(
        root / "data" / "control" / "gate_activation_truth.txt",
        json.dumps(payload, indent=2, default=str)[:16000] + "\n",
    )
    return payload


def write_operator_master_dashboard(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    readiness: Dict[str, Any] = {}
    try:
        from trading_ai.deployment.readiness_decision import compute_final_readiness

        readiness = compute_final_readiness(write_files=False)
    except Exception as exc:
        readiness = {"error": str(exc)}

    trades: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    try:
        from trading_ai.global_layer.trade_truth import load_federated_trades

        trades, fed = load_federated_trades()
        meta = fed if isinstance(fed, dict) else {}
    except Exception:
        pass

    learn_tail = []
    logp = root / "data" / "learning" / "system_learning_log.jsonl"
    if logp.is_file():
        try:
            lines = logp.read_text(encoding="utf-8").splitlines()
            for ln in lines[-8:]:
                try:
                    learn_tail.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "readiness": {
            "ready_for_first_20": readiness.get("ready_for_first_20"),
            "critical_blockers": readiness.get("critical_blockers"),
        },
        "federation": meta,
        "trade_count_sampled": len(trades),
        "learning_tail": learn_tail,
        "sections": {
            "ceo_summary": "See data/review and scoped CEO shells per avenue/gate.",
            "gate_b": "Disabled unless env + validation artifact per gate_activation_truth.json.",
        },
        "honest_classification": "operator_dashboard_advisory",
    }
    _write_json(root / "data" / "control" / "daily_operator_master_dashboard.json", payload)
    _write_txt(
        root / "data" / "control" / "daily_operator_master_dashboard.txt",
        json.dumps(payload, indent=2, default=str)[:24000] + "\n",
    )
    return payload


def write_avenue_gate_trade_rollup(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    by_scope: Dict[str, Any] = {"A": {"gate_a": [], "gate_b": []}, "B": {"gate_b": []}}
    try:
        from trading_ai.global_layer.trade_truth import load_federated_trades

        trades, _ = load_federated_trades()
        for t in trades:
            if not isinstance(t, dict):
                continue
            aid = str(t.get("avenue_id") or "").upper() or "UNKNOWN"
            gid = str(t.get("gate_id") or t.get("trading_gate") or "").lower() or "unknown"
            by_scope.setdefault(aid, {})
            by_scope[aid].setdefault(gid, []).append(
                {
                    "trade_id": t.get("trade_id") or t.get("id"),
                    "product": t.get("product_id") or t.get("market_id"),
                }
            )
    except Exception as exc:
        by_scope = {"error": str(exc)}

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "by_avenue_and_gate": by_scope,
        "contamination_guard": "Keys are avenue_id / gate_id — do not merge across venues.",
        "honest_empty": "If lists empty, no trades in federated layer for this runtime root.",
    }
    _write_json(root / "data" / "control" / "avenue_gate_trade_rollup.json", payload)
    _write_txt(
        root / "data" / "control" / "avenue_gate_trade_rollup.txt",
        json.dumps(payload, indent=2, default=str)[:20000] + "\n",
    )
    return payload


def write_automation_loop_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "auto_runs_now": {
            "shark_scheduler": "when_run_shark_process_with_apscheduler — host-specific",
            "nte_coinbase_engine": "in-process tick — venue-specific",
        },
        "callable_ready_not_scheduler_proven": [
            "trading_ai.learning.lockdown_bundle.refresh_lockdown_artifacts",
            "trading_ai.multi_avenue.lifecycle_hooks.on_daily_cycle",
            "trading_ai.reports.daily_trading_summary.write_daily_trade_snapshot",
        ],
        "scaffold_only": [
            "external_research_pipeline",
            "full_autonomous_daily_scheduler_outside_known_hosts",
        ],
        "depends_on_operator": [
            "manual rerun validation",
            "env toggles for Gate B",
        ],
    }
    _write_json(root / "data" / "control" / "automation_loop_truth.json", payload)
    _write_txt(
        root / "data" / "control" / "automation_loop_truth.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


def write_external_storage_readiness(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_for_external_sync_design": True,
        "external_storage_connected": False,
        "high_value_directories": [
            "data/learning/",
            "data/control/",
            "data/review/",
            "data/reports/",
            "databank/",
        ],
        "append_only_logs": [
            "data/learning/system_learning_log.jsonl",
            "data/control/ratio_change_log.jsonl",
        ],
        "daily_summaries": [
            "data/reports/daily_trading_summary.json",
            "data/review/daily_ai_self_learning_review.json",
        ],
        "encryption_claim": False,
        "note": "No external blob store wired; mirror design only.",
    }
    _write_json(root / "data" / "control" / "external_storage_readiness.json", payload)
    _write_txt(
        root / "data" / "control" / "external_storage_readiness.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


def _git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[3],
        )
        if out.returncode == 0 and (out.stdout or "").strip():
            return (out.stdout or "").strip()[:40]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def write_repo_runtime_deploy_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_present_in_repo": True,
        "local_runtime_artifacts_present": (root / "data").is_dir(),
        "runtime_invocation_proven": "unknown_without_host_telemetry",
        "git_commit_state": _git_rev(),
        "remote_deploy_state": "unknown",
        "host_env_parity_state": "unknown",
        "operator_followup_required": [
            "Confirm EZRAS_RUNTIME_ROOT on trading host matches intended artifact root.",
            "Confirm Gate B env + validation file policy on deploy host.",
        ],
    }
    _write_json(root / "data" / "control" / "repo_runtime_deploy_truth.json", payload)
    _write_txt(
        root / "data" / "control" / "repo_runtime_deploy_truth.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


def write_system_lockdown_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    readiness: Dict[str, Any] = {}
    try:
        from trading_ai.deployment.readiness_decision import compute_final_readiness

        readiness = compute_final_readiness(write_files=False)
    except Exception as exc:
        readiness = {"error": str(exc)}

    blockers = list(readiness.get("critical_blockers") or [])
    locked_val = not bool(readiness.get("ready_for_first_20"))
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "locked_for_rerun_validation": locked_val,
        "locked_for_first_5_live_trades": locked_val,
        "remaining_blockers": blockers,
        "truth_notes": [
            "Gate B is structurally separate from Gate A first-20 readiness.",
            "Self-learning logs are append-only — they do not change execution policy.",
        ],
        "gate_a_ready_scope": "Coinbase Gate A micro-validation / NTE path per governance + runtime policy.",
        "gate_b_ready_scope": "Kalshi / Gate B only when operator enables and validation artifact exists.",
        "future_avenue_auto_attach_ready": True,
        "self_learning_layer_ready": True,
        "external_storage_design_ready": True,
    }
    _write_json(root / "data" / "control" / "system_lockdown_truth.json", payload)
    _write_txt(
        root / "data" / "control" / "system_lockdown_truth.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


def append_ratio_review_event(note: str, *, runtime_root: Optional[Path] = None) -> None:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = root / "data" / "control" / "ratio_change_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "ratio_review_refresh",
        "detail": note,
        "status": "proposal_only_not_executed",
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def write_daily_ratio_review(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    append_ratio_review_event("daily_ratio_review_generated", runtime_root=root)
    qct = _read_json(root / "data" / "control" / "quote_capital_truth.json") or {}
    dcr = _read_json(root / "data" / "control" / "deployable_capital_report.json") or {}
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universal_snapshot": "See data/control/universal_ratio_policy_snapshot.json if present.",
        "quote_capital_truth_present": bool(qct),
        "deployable_capital_report_present": bool(dcr),
        "status": "proposal_only_not_executed",
        "honest_classification": "advisory_readout_not_second_risk_engine",
    }
    _write_json(root / "data" / "review" / "daily_ratio_review.json", payload)
    _write_txt(
        root / "data" / "review" / "daily_ratio_review.txt",
        json.dumps(payload, indent=2, default=str) + "\n",
    )
    return payload


def write_universal_ratio_snapshots(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    existing = _read_json(root / "data" / "control" / "ratio_policy_snapshot.json") or {}
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universal": {
            "global_reserve_ratio": "see governance / operator settings — not overridden here",
            "soft_reserve_ratio": "advisory",
            "hard_reserve_ratio": "advisory",
            "max_daily_drawdown": "enforced by risk engines elsewhere — this file is not a second enforcer",
            "global_concurrency_cap": "venue/gate specific layers extend this",
        },
        "legacy_ratio_policy_snapshot_reference": existing,
        "honest_classification": "documentation_snapshot_not_execution_replacement",
    }
    _write_json(root / "data" / "control" / "universal_ratio_policy_snapshot.json", payload)
    _write_txt(
        root / "data" / "control" / "universal_ratio_policy_snapshot.txt",
        json.dumps(payload, indent=2, default=str)[:12000] + "\n",
    )
    res = _read_json(root / "data" / "control" / "reserve_capital_report.json")
    if res:
        _write_txt(
            root / "data" / "control" / "reserve_capital_report.txt",
            json.dumps(res, indent=2, default=str)[:12000] + "\n",
        )
    payload["reserve_capital_report_txt_written"] = bool(res)
    return payload


def write_final_lockdown_audit(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()

    def cls(name: str, state: str, notes: str) -> Dict[str, Any]:
        return {"subsystem": name, "classification": state, "notes": notes}

    subsystems: List[Dict[str, Any]] = [
        cls(
            "micro_validation",
            "runtime_proven_when_operator_runs_validation_runner",
            "Honest root-cause codes in deployment layer; depends on host credentials.",
        ),
        cls(
            "gate_a_coinbase_execution",
            "code_ready_not_runtime_proven",
            "Engine exists; proof requires live host + policy + balances.",
        ),
        cls(
            "gate_b_kalshi",
            "advisory_only_until_state_c",
            "Explicit env + validation artifact required.",
        ),
        cls(
            "universal_multi_avenue",
            "runtime_proven",
            "lifecycle hooks + scaffolds invoked from post_trade and validation paths.",
        ),
        cls(
            "self_learning_layer",
            "runtime_proven",
            "Append-only logs + daily review callable; not autonomous execution.",
        ),
        cls(
            "scheduler_automation",
            "callable_ready_not_scheduler_proven",
            "APScheduler when run_shark used; not guaranteed in all entrypoints.",
        ),
        cls(
            "external_storage",
            "not_implemented",
            "Design-only flags; no cloud sync connected.",
        ),
    ]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "subsystems": subsystems,
        "summary": "Classifications are honest — advisory layers are not labeled as live execution.",
    }
    _write_json(root / "data" / "control" / "final_lockdown_audit.json", payload)
    lines = ["FINAL LOCKDOWN AUDIT", "=====================", "", json.dumps(payload, indent=2, default=str)]
    _write_txt(root / "data" / "control" / "final_lockdown_audit.txt", "\n".join(lines) + "\n")
    return payload


def refresh_lockdown_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Refresh dashboards, truth files, and learning satellite artifacts (safe to call daily)."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    st = load_authority_state(runtime_root=root)
    save_authority_state(st, runtime_root=root)

    out: Dict[str, Any] = {
        "gate_activation_truth": write_gate_activation_truth(runtime_root=root),
        "operator_dashboard": write_operator_master_dashboard(runtime_root=root),
        "avenue_gate_rollup": write_avenue_gate_trade_rollup(runtime_root=root),
        "automation_loop": write_automation_loop_truth(runtime_root=root),
        "external_storage": write_external_storage_readiness(runtime_root=root),
        "repo_truth": write_repo_runtime_deploy_truth(runtime_root=root),
        "system_lockdown": write_system_lockdown_truth(runtime_root=root),
        "ratio_snapshots": write_universal_ratio_snapshots(runtime_root=root),
        "daily_ratio_review": write_daily_ratio_review(runtime_root=root),
        "performance_tracker": refresh_ai_performance_tracker(runtime_root=root),
        "research_review": write_daily_research_review(runtime_root=root),
        "final_audit": write_final_lockdown_audit(runtime_root=root),
    }
    return out
