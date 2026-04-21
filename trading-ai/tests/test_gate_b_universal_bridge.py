"""Gate B execution proof → universal loop proof bridge (honest mapping only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_bridge_emits_only_when_gate_proof_complete(tmp_path: Path) -> None:
    from trading_ai.universal_execution.gate_b_proof_bridge import try_emit_universal_loop_proof_from_gate_b_file

    ep = tmp_path / "execution_proof"
    ep.mkdir(parents=True, exist_ok=True)
    proof = {
        "FINAL_EXECUTION_PROVEN": True,
        "execution_success": True,
        "coinbase_order_verified": True,
        "databank_written": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "scheduler_stable": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
        "proof_kind": "gate_b_live_micro_coinbase_round_trip",
        "trade_id": "t_bridge_1",
    }
    (ep / "gate_b_live_execution_validation.json").write_text(json.dumps(proof), encoding="utf-8")
    out = try_emit_universal_loop_proof_from_gate_b_file(runtime_root=tmp_path)
    assert out.get("emitted") is True
    p = tmp_path / "data" / "control" / "universal_execution_loop_proof.json"
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("final_execution_proven") is True
    assert data.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") is True


def test_bridge_rejects_partial_gate_proof(tmp_path: Path) -> None:
    from trading_ai.universal_execution.gate_b_proof_bridge import try_emit_universal_loop_proof_from_gate_b_file

    ep = tmp_path / "execution_proof"
    ep.mkdir(parents=True, exist_ok=True)
    proof = {"FINAL_EXECUTION_PROVEN": False, "execution_success": False}
    (ep / "gate_b_live_execution_validation.json").write_text(json.dumps(proof), encoding="utf-8")
    out = try_emit_universal_loop_proof_from_gate_b_file(runtime_root=tmp_path)
    assert out.get("emitted") is False
    assert out.get("blocking_condition")
    assert isinstance(out.get("proof_fields_missing_or_false"), list)
