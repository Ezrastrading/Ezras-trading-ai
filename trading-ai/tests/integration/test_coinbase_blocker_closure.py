"""Blocker closure bundle (accelerated; may require network for public spot)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trading_ai.runtime_proof.coinbase_avenue_a_blocker_closure import (
    evaluate_rubric,
    run_blocker_closure_bundle,
)


def test_blocker_closure_rubric_structure(tmp_path: Path) -> None:
    with patch(
        "trading_ai.runtime_proof.coinbase_avenue_a_blocker_closure.coinbase_public_connectivity_probe",
        return_value={"ok": True, "spot_usd": 1.0, "product_id": "BTC-USD"},
    ):
        rep = run_blocker_closure_bundle(tmp_path, scheduler_stress_ticks=25)
    assert "rubric" in rep
    r = rep["rubric"]
    assert "1_paper_sandbox_session" in r
    assert rep["hard_stop_close_chain"].get("packet_hard_stop_events", 0) >= 1


def test_evaluate_rubric_supabase_requires_doc(tmp_path: Path) -> None:
    rep = {
        "coinbase_public_connectivity": {"ok": True},
        "base_runtime_proof": {
            "close_chain": {
                "merged_trade": {"trade_id": "x", "avenue": "coinbase"},
                "nte_entry_gate": {"ok": True},
            }
        },
        "hard_stop_close_chain": {
            "hard_stop": True,
            "exit_reason": "stop_loss",
            "packet_hard_stop_events": 1,
            "merged_trade": {"hard_stop_exit": True, "exit_reason": "stop_loss"},
        },
        "scheduler_tick_stress": {"ok": True},
        "scheduler_apscheduler_probe": {"ok": True, "single_ai_review_tick_id": True},
        "supabase_stance": "local_first_v1",
    }
    r = evaluate_rubric(rep)
    assert r["4_supabase_stance"] == "pass"
