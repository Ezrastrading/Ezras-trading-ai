"""Normalized daemon verification contract — rows, enums, honesty fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

AdapterTruthClass = Literal[
    "fully_fake_adapter",
    "venue_shaped_fake_adapter",
    "simulated_real_artifact_replay",
    "real_runtime_proof_reference_present",
    "no_proof_available",
]

ExecutionMode = Literal["tick_only", "supervised_live", "autonomous_live"]

PassClassification = Literal["PASS", "FAIL", "NOT_WIRED", "SKIPPED"]

ProofStrength = Literal[
    "fake_logic_only",
    "replay_compatibility_only",
    "live_proof_file_compatible_only",
    "runtime_proven_strict",
    "none",
]


@dataclass(frozen=True)
class ScenarioDef:
    """One lifecycle / failure scenario — interpreted by fake + replay harnesses."""

    scenario_id: str
    title: str
    category: Literal["lifecycle", "failure_injection", "restart"]


@dataclass
class DaemonMatrixRow:
    """Normalized result row — must not collapse honesty across avenues/gates."""

    avenue_id: str
    avenue_name: str
    gate_id: str
    scenario_id: str
    scenario_title: str
    execution_mode: ExecutionMode
    adapter_truth_class: AdapterTruthClass
    orders_attempted: bool
    entry_attempted: bool
    entry_filled: bool
    exit_attempted: bool
    exit_filled: bool
    pnl_verified: bool
    local_write_ok: bool
    remote_write_ok: bool
    governance_ok: bool
    review_ok: bool
    ready_for_rebuy: bool
    rebuy_attempted: bool
    rebuy_allowed: bool
    rebuy_block_reason: str
    daemon_abort_triggered: bool
    final_state: str
    pass_classification: PassClassification
    proof_strength: ProofStrength
    blocking_reason: str
    notes: str
    # Honesty extensions (explicit — never implied across avenues/gates)
    fake_logic_proven: bool
    replay_logic_proven: bool
    live_proof_compatible: bool
    autonomous_live_runtime_proven: bool
    avenue_live_execution_wired: bool
    gate_contract_wired: bool
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "avenue_id": self.avenue_id,
            "avenue_name": self.avenue_name,
            "gate_id": self.gate_id,
            "scenario_id": self.scenario_id,
            "scenario_title": self.scenario_title,
            "execution_mode": self.execution_mode,
            "adapter_truth_class": self.adapter_truth_class,
            "orders_attempted": self.orders_attempted,
            "entry_attempted": self.entry_attempted,
            "entry_filled": self.entry_filled,
            "exit_attempted": self.exit_attempted,
            "exit_filled": self.exit_filled,
            "pnl_verified": self.pnl_verified,
            "local_write_ok": self.local_write_ok,
            "remote_write_ok": self.remote_write_ok,
            "governance_ok": self.governance_ok,
            "review_ok": self.review_ok,
            "ready_for_rebuy": self.ready_for_rebuy,
            "rebuy_attempted": self.rebuy_attempted,
            "rebuy_allowed": self.rebuy_allowed,
            "rebuy_block_reason": self.rebuy_block_reason,
            "daemon_abort_triggered": self.daemon_abort_triggered,
            "final_state": self.final_state,
            "pass_classification": self.pass_classification,
            "proof_strength": self.proof_strength,
            "blocking_reason": self.blocking_reason,
            "notes": self.notes,
            "fake_logic_proven": self.fake_logic_proven,
            "replay_logic_proven": self.replay_logic_proven,
            "live_proof_compatible": self.live_proof_compatible,
            "autonomous_live_runtime_proven": self.autonomous_live_runtime_proven,
            "avenue_live_execution_wired": self.avenue_live_execution_wired,
            "gate_contract_wired": self.gate_contract_wired,
        }
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


def proof_flags_for_row(
    *,
    adapter_truth_class: AdapterTruthClass,
    pass_ok: bool,
    avenue_live_wired: bool,
    gate_wired: bool,
) -> Dict[str, Any]:
    """
    Honesty: matrix rows never set autonomous_live_runtime_proven True — only external
    operator-stamped runtime artifacts may claim that (see final truth writers).
    """
    _ = avenue_live_wired, gate_wired  # explicit: no cross-inheritance of proof
    fake_ok = bool(
        pass_ok
        and adapter_truth_class
        in ("fully_fake_adapter", "venue_shaped_fake_adapter")
    )
    replay_ok = bool(pass_ok and adapter_truth_class == "simulated_real_artifact_replay")
    live_compat = bool(
        pass_ok and adapter_truth_class == "real_runtime_proof_reference_present"
    )
    return {
        "fake_logic_proven": fake_ok,
        "replay_logic_proven": replay_ok,
        "live_proof_compatible": live_compat,
        "autonomous_live_runtime_proven": False,
    }
