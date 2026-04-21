"""
Scheduler / runner honesty for Gate B Coinbase — no false claims of a shipped long-running daemon.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def build_gate_b_loop_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    tick_path = root / "data" / "control" / "gate_b_last_production_tick.json"
    last_tick = None
    if tick_path.is_file():
        try:
            last_tick = json.loads(tick_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_tick = None

    production_loop_proven = bool(
        isinstance(last_tick, dict) and last_tick.get("tick_ok") is True and (last_tick.get("generated_at"))
    )

    # Section 6 — strict yes/no (honest; tick never submits orders).
    tick_one_pass = {
        "gate_b_tick_sufficient_for_one_production_safe_evaluation_pass": True,
        "evaluation_pass_includes_orders": False,
        "note": (
            "`gate-b-tick` runs gate_b-scoped adaptive eval + engine on scan rows — adequate for one **scan/adaptive** "
            "evaluation cycle. It does not prove order execution safety; live orders use the separate Coinbase path."
        ),
        "shipped_in_repo_continuous_gate_b_loop_command": False,
        "external_cron_systemd_intended_for_repeated_production_ticks": True,
        "daemon_absence_blocks": "full_autonomous_production_only_not_live_order_activation",
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "dedicated_gate_b_runner_exists": True,
        "dedicated_gate_b_runner_module": "trading_ai.deployment.gate_b_production_tick",
        "dedicated_gate_b_scheduler_exists": False,
        "dedicated_gate_b_scheduler_note": (
            "No APScheduler job registered solely for Coinbase Gate B in this repo. "
            "Use cron/systemd to run `python -m trading_ai.deployment gate-b-tick` on an interval."
        ),
        "gate_b_continuous_command": "python -m trading_ai.deployment gate-b-tick",
        "uses_gate_b_scoped_adaptive": True,
        "uses_gate_b_scoped_failsafe": "Order paths use execution_gate=gate_b when wired; tick is scan-only.",
        "uses_gate_b_scoped_ledger_labels": "Live orders must use gate_id=gate_b on Coinbase path (see coinbase client).",
        "production_loop_proven": production_loop_proven,
        "production_loop_missing_reason": (
            None
            if production_loop_proven
            else "No successful gate_b_last_production_tick.json yet, or not scheduled externally."
        ),
        "honesty": (
            "Continuous live operation is operator-scheduled ticks + future order wiring — not an implicit "
            "in-process infinite loop in this repository."
        ),
        "continuous_loop_semantics": {
            "live_micro_proven": "See gate_b_live_status.json — not defined here.",
            "live_order_ready": "See gate_b_ready_for_live_orders in gate_b_live_status.json.",
            "repeated_tick_ready": "Command exists; repetition requires operator/cron — see gate_b_continuous_command.",
            "continuous_loop_ready": False,
            "full_autonomous_production_ready": False,
            "continuous_loop_ready_meaning": "No in-repo always-on Gate B daemon; use external scheduler for repetition.",
        },
        **{k: v for k, v in tick_one_pass.items() if k != "note"},
        "tick_one_pass_details": tick_one_pass,
    }


def write_gate_b_loop_truth_artifacts(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    rep = root / "data" / "reports"
    ctrl.mkdir(parents=True, exist_ok=True)
    rep.mkdir(parents=True, exist_ok=True)
    payload = build_gate_b_loop_truth(runtime_root=root)
    contract = {
        "tick_entrypoint": "trading_ai.deployment.gate_b_production_tick.run_gate_b_production_tick",
        "orders_in_tick": False,
        "adaptive_scope": "gate_b",
        "persist_optional_env": "GATE_B_PRODUCTION_TICK_PERSIST_ADAPTIVE=true",
    }
    runbook = {
        "start": "export EZRAS_RUNTIME_ROOT=... && export PYTHONPATH=src && python3 -m trading_ai.deployment gate-b-tick",
        "stop": "Stop the external scheduler process (cron/systemd) — no in-repo daemon to kill.",
        "verify": "data/control/gate_b_last_production_tick.json and adaptive_live_proof.json",
    }
    (ctrl / "gate_b_loop_truth.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (ctrl / "gate_b_loop_truth.txt").write_text(json.dumps(payload, indent=2)[:8000] + "\n", encoding="utf-8")
    (ctrl / "gate_b_runner_contract.json").write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    (rep / "gate_b_operator_runbook.json").write_text(json.dumps(runbook, indent=2) + "\n", encoding="utf-8")
    (rep / "gate_b_operator_runbook.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in runbook.items()) + "\n",
        encoding="utf-8",
    )
    return {
        "generated_at": payload["generated_at"],
        "paths": {
            "gate_b_loop_truth.json": str(ctrl / "gate_b_loop_truth.json"),
            "gate_b_runner_contract.json": str(ctrl / "gate_b_runner_contract.json"),
            "gate_b_operator_runbook.json": str(rep / "gate_b_operator_runbook.json"),
        },
    }
