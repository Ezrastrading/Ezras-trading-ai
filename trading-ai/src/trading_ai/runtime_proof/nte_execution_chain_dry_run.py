"""
Dry-run NTE entry gate chain (no orders): governance → strategy firewall.

Writes ``execution_proof/execution_chain_validation.jsonl`` under runtime root.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from trading_ai.nte.execution.coinbase_engine import _nte_entry_gates_coinbase


def _healthy_joint(gdir: Path) -> None:
    p = gdir / "joint_review_latest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "joint_review_id": "jr_dryrun",
                "packet_id": "pkt_dryrun",
                "empty": False,
                "live_mode_recommendation": "normal",
                "review_integrity_state": "full",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ),
        encoding="utf-8",
    )


def run_dry_run_probes(runtime_root: Path, *, n: int = 5) -> Path:
    """
    Run ``n`` simulated probes through :func:`_nte_entry_gates_coinbase` with
    ``live_routing_permitted`` patched — **no venue orders**.

    ``execution_allowed`` is True only when both governance and strategy gate pass.
    """
    runtime_root = runtime_root.resolve()
    out_dir = runtime_root / "execution_proof"
    out_dir.mkdir(parents=True, exist_ok=True)
    jl = out_dir / "execution_chain_validation.jsonl"

    saved = os.environ.get("EZRAS_RUNTIME_ROOT")
    gdir = runtime_root / "shark" / "memory" / "global"
    lines: List[str] = []

    probes: List[Dict[str, Any]] = [
        {"intent_id": "dry_1", "strategy": "mean_reversion", "rb": "range|a", "live_ok": True},
        {"intent_id": "dry_2", "strategy": "continuation_pullback", "rb": "trend|b", "live_ok": True},
        {"intent_id": "dry_3", "strategy": "micro_momentum", "rb": "unknown|c", "live_ok": True},
        {"intent_id": "dry_4", "strategy": "blocked_route", "rb": "x|y", "live_ok": False},
        {"intent_id": "dry_5", "strategy": "final_probe", "rb": "z|z", "live_ok": True},
    ]

    try:
        os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
        os.environ.setdefault("GOVERNANCE_ORDER_ENFORCEMENT", "true")
        os.environ.setdefault("GOVERNANCE_MISSING_JOINT_BLOCKS", "true")
        os.environ.setdefault("GOVERNANCE_STALE_JOINT_BLOCKS", "true")
        os.environ.setdefault("GOVERNANCE_UNKNOWN_MODE_BLOCKS", "true")
        os.environ.setdefault("GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS", "true")
        os.environ.setdefault("GOVERNANCE_CAUTION_BLOCK_ENTRIES", "true")

        _healthy_joint(gdir)

        for pr in probes[:n]:

            def _make_live(ok: bool):
                def _inner(_sid: str, **_kw: Any) -> bool:
                    return ok

                return _inner

            with patch(
                "trading_ai.nte.execution.coinbase_engine.live_routing_permitted",
                side_effect=_make_live(pr["live_ok"]),
            ):
                ok_gate, fail_kind, detail = _nte_entry_gates_coinbase(
                    product_id=pr["intent_id"],
                    strategy_route_label=pr["strategy"],
                    route_bucket=pr["rb"],
                )
            # If fail_kind is "strategy", governance allowed entry to strategy check.
            gov_allowed = fail_kind != "governance"
            rec: Dict[str, Any] = {
                "intent_id": pr["intent_id"],
                "governance_decision": {
                    "allowed": gov_allowed,
                    "failure_kind": fail_kind if fail_kind == "governance" else None,
                    "reason_detail": detail if fail_kind == "governance" else None,
                },
                "route_bucket": pr["rb"],
                "execution_allowed": ok_gate,
                "strategy_firewall_permitted_simulated": pr["live_ok"],
                "final_decision_reason": detail if not ok_gate else "ok",
                "trace": "coinbase_engine._nte_entry_gates_coinbase → check_new_order_allowed_full → live_routing_permitted (mocked)",
            }
            lines.append(json.dumps(rec, default=str))

    finally:
        if saved is not None:
            os.environ["EZRAS_RUNTIME_ROOT"] = saved
        else:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)

    jl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jl
