"""Integration: Coinbase Avenue A shadow/paper runtime proof harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.runtime_proof.coinbase_shadow_paper_pass import run_full_proof


def test_runtime_proof_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    root.mkdir()
    report = run_full_proof(root)

    assert (root / "RUNTIME_PROOF_REPORT.json").is_file()
    assert (root / "governance_gate_decisions.log").is_file()

    sched = report.get("scheduler") or {}
    assert sched.get("ok") is True
    assert sched.get("single_ai_review_tick_id") is True
    assert sched.get("max_instances") == 1
    assert sched.get("coalesce") is True

    close = report.get("close_chain") or {}
    assert close.get("nte_entry_gate", {}).get("ok") is True
    merged = close.get("merged_trade") or {}
    assert merged.get("trade_id") == "cb_rt_proof_001"
    assert merged.get("avenue") == "coinbase"
    assert float(merged.get("net_pnl_usd") or 0) == pytest.approx(2.1)
    prov = merged.get("truth_provenance") or {}
    assert prov.get("primary") == "nte_trade_memory"

    tm = json.loads((root / "shark" / "nte" / "memory" / "trade_memory.json").read_text(encoding="utf-8"))
    assert len(tm.get("trades") or []) == 1
