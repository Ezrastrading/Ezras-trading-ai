"""Gap 1 — databank path must be explicit; zero cross-contamination between isolated roots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest

from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.nte.databank.local_trade_store import DatabankRootUnsetError, resolve_databank_root
from trading_ai.nte.memory.store import MemoryStore


def test_resolve_databank_prefers_explicit_env_over_ezras(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = tmp_path / "explicit_db"
    a.mkdir()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path / "rt"))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(a))
    root, src = resolve_databank_root()
    assert root.resolve() == a.resolve()
    assert src == "TRADE_DATABANK_MEMORY_ROOT"


def test_resolve_databank_derives_from_ezras_runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_DATABANK_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    root, src = resolve_databank_root()
    assert root == tmp_path / "databank"
    assert src == "EZRAS_RUNTIME_ROOT/databank"


def test_databank_root_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRADE_DATABANK_MEMORY_ROOT", raising=False)
    monkeypatch.delenv("EZRAS_RUNTIME_ROOT", raising=False)
    with pytest.raises(DatabankRootUnsetError):
        resolve_databank_root()


def test_two_sessions_zero_cross_contamination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent runtime + databank roots must not share federated trade rows."""
    r1 = tmp_path / "run_a"
    r2 = tmp_path / "run_b"
    d1 = tmp_path / "db_a"
    d2 = tmp_path / "db_b"
    for p in (r1, r2, d1, d2):
        p.mkdir(parents=True)

    def _session(root: Path, db: Path, tid: str, pnl: float) -> Tuple[List[str], str]:
        monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
        monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(db))
        ms = MemoryStore()
        ms.ensure_defaults()
        ms.append_trade(
            {
                "trade_id": tid,
                "avenue": "coinbase",
                "route_bucket": "iso",
                "net_pnl_usd": pnl,
            }
        )
        line = json.dumps(
            {
                "trade_id": tid + "_db_only",
                "avenue_name": "coinbase",
                "net_pnl": pnl + 1.0,
                "timestamp_open": "2026-01-01T00:00:00+00:00",
                "timestamp_close": "2026-01-01T00:05:00+00:00",
            }
        )
        (db / "trade_events.jsonl").write_text(line + "\n", encoding="utf-8")
        trades, meta = load_federated_trades(nte_store=ms)
        ids = sorted({str(t.get("trade_id")) for t in trades})
        return ids, str(meta.get("databank_root") or "")

    ids1, mroot1 = _session(r1, d1, "alpha_only", 1.0)
    ids2, mroot2 = _session(r2, d2, "beta_only", 2.0)

    assert "alpha_only" in ids1 and "alpha_only_db_only" in ids1
    assert "beta_only" in ids2 and "beta_only_db_only" in ids2
    assert not set(ids1) & set(ids2)
    assert mroot1 == str(d1.resolve())
    assert mroot2 == str(d2.resolve())


def test_federation_row_count_deterministic_with_empty_peer_databank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "databank"))
    (tmp_path / "databank").mkdir(parents=True)
    ms = MemoryStore()
    ms.ensure_defaults()
    tm = ms.load_json("trade_memory.json")
    tm["trades"] = [{"trade_id": f"t{i}", "avenue": "coinbase", "net_pnl_usd": 1.0} for i in range(10)]
    ms.save_json("trade_memory.json", tm)
    trades, meta = load_federated_trades(nte_store=ms)
    assert len(trades) == 10
    assert meta["merged_trade_count"] == 10
