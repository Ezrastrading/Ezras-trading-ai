"""LEVEL 2 — Replay proof-shaped artifacts; exercises merge/parse paths — not live proof."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.daemon_testing.contract import DaemonMatrixRow
from trading_ai.daemon_testing.daemon_fake_adapters import build_fake_row
from trading_ai.daemon_testing.registry import AvenueBinding, GateBinding


def load_replay_bundle(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def merge_prior_from_replay(
    replay: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Shape expected by can_open_next_trade_after."""
    loop = replay.get("universal_execution_loop_proof") or replay
    if not isinstance(loop, dict):
        return None
    ls = loop.get("lifecycle_stages") or {}
    if not isinstance(ls, dict):
        ls = {}
    return {
        "final_execution_proven": loop.get("final_execution_proven"),
        "terminal_honest_state": loop.get("terminal_honest_state"),
        "entry_fill_confirmed": ls.get("entry_fill_confirmed"),
        "exit_fill_confirmed": ls.get("exit_fill_confirmed"),
        "pnl_verified": ls.get("pnl_verified"),
        "local_write_ok": ls.get("local_write_ok"),
    }


def build_replay_row(
    *,
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    execution_mode: str,
    replay_path: Optional[Path],
) -> DaemonMatrixRow:
    base = build_fake_row(
        avenue=avenue,
        gate=gate,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        adapter_truth_class="fully_fake_adapter",
    )
    rp = load_replay_bundle(Path(replay_path) if replay_path else Path("/nonexistent"))
    prior = merge_prior_from_replay(rp)
    notes = base.notes + " | replay: "
    replay_ok = False
    if not prior:
        notes += "no_fixture_or_empty_replay — row uses fake skeleton only"
    else:
        from trading_ai.universal_execution.rebuy_policy import can_open_next_trade_after

        ok, why = can_open_next_trade_after(prior)
        notes += f"rebuy_policy_on_fixture: ok={ok} reason={why}"
        replay_ok = bool(ok and base.pass_classification == "PASS")

    ex: Dict[str, Any] = dict(base.extra or {})
    ex["replay_fixture"] = str(replay_path) if replay_path else None

    return DaemonMatrixRow(
        avenue_id=base.avenue_id,
        avenue_name=base.avenue_name,
        gate_id=base.gate_id,
        scenario_id=base.scenario_id,
        scenario_title=base.scenario_title,
        execution_mode=base.execution_mode,
        adapter_truth_class="simulated_real_artifact_replay",
        orders_attempted=base.orders_attempted,
        entry_attempted=base.entry_attempted,
        entry_filled=base.entry_filled,
        exit_attempted=base.exit_attempted,
        exit_filled=base.exit_filled,
        pnl_verified=base.pnl_verified,
        local_write_ok=base.local_write_ok,
        remote_write_ok=base.remote_write_ok,
        governance_ok=base.governance_ok,
        review_ok=base.review_ok,
        ready_for_rebuy=base.ready_for_rebuy,
        rebuy_attempted=base.rebuy_attempted,
        rebuy_allowed=base.rebuy_allowed,
        rebuy_block_reason=base.rebuy_block_reason,
        daemon_abort_triggered=base.daemon_abort_triggered,
        final_state=base.final_state,
        pass_classification=base.pass_classification,
        proof_strength="replay_compatibility_only",
        blocking_reason=base.blocking_reason,
        notes=notes,
        fake_logic_proven=False,
        replay_logic_proven=replay_ok,
        live_proof_compatible=False,
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=base.avenue_live_execution_wired,
        gate_contract_wired=base.gate_contract_wired,
        extra=ex,
    )
