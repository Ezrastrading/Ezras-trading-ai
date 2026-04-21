"""verify_data_pipeline_after_trade — trade_events id detection must not rely on short tail only."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_verify_pipeline_finds_trade_id_in_large_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    db = tmp_path / "databank"
    db.mkdir(parents=True, exist_ok=True)
    te = db / "trade_events.jsonl"
    filler = json.dumps({"trade_id": "old", "x": "y"}) + "\n"
    te.write_text(filler * 500 + json.dumps({"trade_id": "target_tid", "net_pnl": 0.0}) + "\n", encoding="utf-8")

    (tmp_path / "shark" / "nte" / "memory").mkdir(parents=True, exist_ok=True)
    tm = tmp_path / "shark" / "nte" / "memory" / "trade_memory.json"
    tm.write_text(json.dumps({"trades": [{"trade_id": "target_tid"}]}), encoding="utf-8")

    (tmp_path / "shark" / "memory" / "global").mkdir(parents=True, exist_ok=True)
    rp = tmp_path / "shark" / "memory" / "global" / "review_packet_latest.json"
    rp.write_text(json.dumps({"packet_id": "p1", "review_type": "midday"}), encoding="utf-8")

    glog = tmp_path / "governance_gate_decisions.log"
    glog.write_text('governance_gate_decision {"operation":"live_execution_validation"}\n', encoding="utf-8")

    from trading_ai.runtime_proof.live_execution_validation import verify_data_pipeline_after_trade

    with patch(
        "trading_ai.runtime_proof.live_execution_validation.load_federated_trades",
        return_value=([{"trade_id": "target_tid"}], {}),
    ):
        with patch(
            "trading_ai.runtime_proof.live_execution_validation._supabase_row_exists_with_retry",
            return_value=True,
        ):
            out = verify_data_pipeline_after_trade(
                tmp_path,
                "target_tid",
                process_stages={"supabase_trade_events": True},
            )
    assert out.get("trade_events_appended") is True
