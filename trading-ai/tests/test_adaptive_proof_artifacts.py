"""Adaptive live + routing proof files: writers, validators, readiness hooks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.control.adaptive_proof_validation import (
    validate_adaptive_live_proof_file,
    validate_adaptive_routing_proof_file,
)
from trading_ai.control.adaptive_routing_live import adaptive_routing_proof_path, compute_live_gate_allocation
from trading_ai.control.live_adaptive_integration import (
    adaptive_live_proof_path,
    coinbase_entry_adaptive_gate,
    write_adaptive_live_proof_file,
)
from trading_ai.control.operating_mode_types import OperatingMode, OperatingOutcome
from trading_ai.deployment.deployment_models import iso_now


def test_routing_proof_written_on_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = compute_live_gate_allocation(aos_report=None, market_quality_allows_adaptive=False)
    assert out.get("allocation_source") == "fallback_static_route"
    rp = adaptive_routing_proof_path()
    assert rp.is_file()
    raw = json.loads(rp.read_text(encoding="utf-8"))
    assert raw.get("proof_source", "").startswith("trading_ai.")
    assert raw.get("recommended_gate_allocations", {}).get("gate_a") is not None
    v = validate_adaptive_routing_proof_file(rp)
    assert v.get("ok") is True


def test_live_proof_validator_accepts_minimal_valid_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = adaptive_live_proof_path()
    payload = {
        "generated_at": iso_now(),
        "current_operating_mode": "normal",
        "mode": "normal",
        "allow_new_trades": True,
        "proof_source": "trading_ai.control.live_adaptive_integration:test",
        "snapshot_inputs": {"x": 1},
    }
    write_adaptive_live_proof_file(payload)
    v = validate_adaptive_live_proof_file(p)
    assert v.get("ok") is True


def test_coinbase_entry_blocked_still_writes_proof_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    out = OperatingOutcome(
        mode=OperatingMode.HALTED,
        prior_mode=OperatingMode.NORMAL,
        mode_change_reasons=["test_halt"],
        emergency_brake_triggered=True,
        size_multiplier_effective=0.0,
        allow_new_trades=False,
        diagnosis={},
        report={
            "recommended_gate_allocations": {"gate_a": 0.5, "gate_b": 0.5},
            "confidence_scaling_ready": False,
        },
        critical_alerts=[],
    )
    with patch(
        "trading_ai.control.live_adaptive_integration.evaluate_adaptive_operating_system",
        return_value=out,
    ):
        ag = coinbase_entry_adaptive_gate(
            equity=1000.0,
            rolling_equity_high=1000.0,
            market_regime="neutral",
            market_chop_score=0.2,
            slippage_health=0.9,
            liquidity_health=0.9,
            product_id="BTC-USD",
        )
    assert ag.get("block_new_entries") is True
    p = adaptive_live_proof_path()
    assert p.is_file() and p.stat().st_size > 40
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw.get("decision_block_new_entries") is True
    assert raw.get("proof_source", "").startswith("trading_ai.")
