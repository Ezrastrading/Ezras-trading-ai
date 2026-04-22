from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_write_daily_crypto_learning_distillation_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Seed minimal trade learning jsonl
    tl = tmp_path / "data" / "learning" / "trade_learning_objects.jsonl"
    tl.parent.mkdir(parents=True, exist_ok=True)
    tl.write_text(
        json.dumps(
            {
                "truth_version": "trade_learning_object_v1",
                "trade_id": "t1",
                "gate": "gate_b",
                "symbol": "BTC-USD",
                "net_pnl_usd": 1.0,
                "fees_usd": 0.1,
                "slippage_estimate_bps": 10.0,
                "hold_duration_sec": 30,
                "entry_reason": "breakout",
                "exit_reason": "target",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    from trading_ai.intelligence.crypto_intelligence.distillation import write_daily_crypto_learning_distillation

    out = write_daily_crypto_learning_distillation(runtime_root=tmp_path)
    assert out.get("ok") is True
    p = tmp_path / "data" / "learning" / "crypto_intelligence" / "daily_distillation.json"
    assert p.is_file()

