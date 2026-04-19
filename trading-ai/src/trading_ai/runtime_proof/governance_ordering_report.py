"""
Static report: NTE / execution entry ordering — governance must run before strategy approval.

Writes ``governance_ordering_report.json`` under the given runtime root.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


def build_governance_ordering_report() -> Dict[str, Any]:
    return {
        "schema": "governance_ordering_report_v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "first_decisive_gate": "joint_review_governance",
        "ordering_statement": (
            "Governance (check_new_order_allowed_full) is the first decisive gate on the NTE Coinbase "
            "entry path; strategy live-routing approval (live_routing_permitted) runs only after governance allows."
        ),
        "nte_coinbase_entry_path": {
            "module": "trading_ai.nte.execution.coinbase_engine",
            "entry_method": "CoinbaseNTEngine._maybe_enter",
            "gate_helper": "_nte_entry_gates_coinbase",
            "ordered_steps": [
                "normalized_entry_intent: pick_live_route → RouterDecision + StrategySignal",
                "governance_first: check_new_order_allowed_full(venue=coinbase, operation=nte_new_entry, intent_id=product_id, route=strategy label, route_bucket=router metadata, strategy_class=metadata)",
                "governance_decision_logged: log_decision=True on governance call",
                "execution_policy_second: live_routing_permitted(strategy_route_label)",
                "order_submission: only after both pass (_place_limit_buy / market buy / paper log)",
            ],
        },
        "shark_execution_chain_reference": {
            "module": "trading_ai.shark.execution",
            "note": "Doctrine then governance (1b) before phase limits; venue submit re-logs governance at API boundary in execution_live.",
        },
        "strategy_approval_is_not_top_level_safety": True,
    }


def write_governance_ordering_report(runtime_root: Path) -> Path:
    runtime_root = runtime_root.resolve()
    d = runtime_root / "governance_proof"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "governance_ordering_report.json"
    p.write_text(json.dumps(build_governance_ordering_report(), indent=2), encoding="utf-8")
    return p
