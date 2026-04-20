"""First supervised trade command center — Avenue A, Gate B-aware (artifact truth only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from trading_ai.org_organism.io_utils import write_json_atomic
from trading_ai.org_organism.paths import first_supervised_trade_command_center_path, first_supervised_trade_runbook_path
from trading_ai.shark.coinbase_spot.avenue_a_operator_status import build_avenue_a_operator_status
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_first_supervised_command_center(*, runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    av = build_avenue_a_operator_status(runtime_root=root)
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    sup = ad.read_json("data/control/avenue_a_supervised_live_truth.json") or {}
    gb = ad.read_json("data/control/gate_b_live_status.json") or {}
    gb_val = ad.read_json("data/control/gate_b_validation.json") or {}

    ready_avenue = "A" if bool(auth.get("avenue_a_can_run_supervised_live_now")) else None
    ready_gate = "gate_b" if bool(gb.get("gate_b_ready_for_live")) else "gate_a"

    payload: Dict[str, Any] = {
        "truth_version": "first_supervised_trade_command_center_v1",
        "generated_at": _now_iso(),
        "advisory_only": True,
        "does_not_enable_autonomous": True,
        "what_avenue_is_ready_now": ready_avenue or "none_until_authority_green",
        "what_gate_is_ready_now": ready_gate,
        "product_selection_mode": str(av.get("gate_a_market_truth_source") or "see_gate_a_universe_snapshot"),
        "configured_quote_usd_typical": 10.0,
        "data_sinks": [
            "data/control/avenue_a_supervised_live_truth.json",
            "data/deployment/supabase_proof.jsonl",
            "execution_proof/* (when harness writes)",
        ],
        "proof_files_expected": [
            "data/control/daemon_live_switch_authority.json",
            "data/control/avenue_a_supervised_live_truth.json",
            "execution_proof/gate_b_live_execution_validation.json (Gate B micro)",
        ],
        "success_means": "Round-trip completes with strict proof artifacts consistent with policy; no contradiction in truth chain.",
        "failure_means": "Any halt, proof mismatch, venue error, or governance fail-closed — stop immediately.",
        "when_to_stop": "On first proof failure, governance block, or operator halt.",
        "when_to_continue": "Only when rollup answers in controlled_live_readiness stay green and operator confirms.",
        "when_not_to_proceed": "When credentials, SSL, schema, or supervised truth are stale or missing.",
        "gate_b_context": {
            "gate_b_live_status_excerpt": {
                "gate_b_ready_for_live": gb.get("gate_b_ready_for_live"),
                "policy_blocked": gb.get("gate_b_disabled_by_runtime_policy"),
            },
            "gate_b_validation_excerpt": {k: gb_val.get(k) for k in ("live_venue_micro_validation_pass", "micro_validation_pass") if k in gb_val},
        },
        "capital_split_reference": (av.get("capital") or {}),
    }
    write_json_atomic(first_supervised_trade_command_center_path(root), payload)

    md = "\n".join(
        [
            "# First supervised trade — operator runbook",
            "",
            f"Generated: {payload['generated_at']}",
            "",
            "## Preconditions",
            "- `python -m trading_ai.deployment supervised-readiness-closer` shows no blocking checklist failures.",
            "- Operator present; small quote only (default 10 USD class).",
            "",
            "## Commands",
            "1. `python -m trading_ai.deployment refresh-runtime-artifacts`",
            "2. `python -m trading_ai.deployment controlled-live-readiness`",
            "3. `python -m trading_ai.deployment avenue-a-daemon-once --quote-usd 10 --product-id BTC-USD`",
            "",
            "## After",
            "- Inspect `data/control/avenue_a_supervised_live_truth.json`",
            "- If Gate B path: confirm `execution_proof/gate_b_live_execution_validation.json` when applicable",
            "",
            "## Honesty",
            "- This runbook does not grant autonomous live or raise exposure.",
            "",
        ]
    )
    rp = first_supervised_trade_runbook_path(root)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(md, encoding="utf-8")

    return payload
