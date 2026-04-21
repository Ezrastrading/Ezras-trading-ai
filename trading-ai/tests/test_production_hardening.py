"""Failure and accounting tests for the production hardening layer."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_ai.shark.production_hardening.double_entry import (
    AccountingMismatchAbort,
    append_trade_ledger,
    assert_double_entry_balanced,
    record_fill_from_execution,
)
from trading_ai.shark.production_hardening.order_identity import (
    is_duplicate_client_order_id,
    register_client_order_id,
)
from trading_ai.shark.production_hardening.price_feed_guard import check_price_sanity
from trading_ai.shark.production_hardening.replay import run_replay_validation
from trading_ai.shark.production_hardening.risk_ruin import compute_rolling_stats


def test_double_entry_balanced_buy_style() -> None:
    lines = [
        {"leg": "debit", "account": "usd", "usd_equiv": 100.0},
        {"leg": "debit", "account": "fee", "usd_equiv": 0.5},
        {"leg": "credit", "account": "base", "usd_equiv": 100.5},
    ]
    assert_double_entry_balanced(lines)


def test_double_entry_rejects_unbalanced() -> None:
    lines = [
        {"leg": "debit", "account": "usd", "usd_equiv": 100.0},
        {"leg": "credit", "account": "base", "usd_equiv": 99.0},
    ]
    with pytest.raises(AccountingMismatchAbort):
        assert_double_entry_balanced(lines)


def test_order_id_ttl_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.shark.production_hardening import paths as ph_paths

    monkeypatch.setattr(ph_paths, "recent_order_ids_json", lambda: tmp_path / "roid.json")
    monkeypatch.setenv("PRODUCTION_HARDENING_LAYER", "1")

    cid = str(uuid.uuid4())
    assert not is_duplicate_client_order_id(cid)
    register_client_order_id(cid, venue="kalshi")
    assert is_duplicate_client_order_id(cid)


def test_price_stale_rejects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trading_ai.shark.production_hardening import paths as ph_paths

    monkeypatch.setattr(ph_paths, "price_tick_state_json", lambda: tmp_path / "tick.json")
    monkeypatch.setenv("PRODUCTION_HARDENING_LAYER", "1")
    monkeypatch.setenv("MAX_STALE_PRICE_MS", "1000")
    old = time.time() - 10.0
    ok, why = check_price_sanity(product_key="BTC-USD", price=50000.0, price_ts_unix=old)
    assert not ok
    assert "stale" in why


def test_replay_empty_ledger_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trading_ai.shark.production_hardening.replay.trade_ledger_jsonl",
        lambda: tmp_path / "empty.jsonl",
    )
    rep = run_replay_validation(last_n=100)
    assert rep["ok"]
    assert rep["rows"] == 0


def test_replay_validates_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "tl.jsonl"
    rec = {
        "id": "1",
        "lines": [
            {"leg": "debit", "account": "usd", "usd_equiv": 10.0},
            {"leg": "credit", "account": "base", "usd_equiv": 10.0},
        ],
    }
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "trading_ai.shark.production_hardening.replay.trade_ledger_jsonl",
        lambda: p,
    )
    rep = run_replay_validation(last_n=10)
    assert rep["ok"]
    assert rep["rows"] == 1


def test_rolling_stats() -> None:
    s = compute_rolling_stats([1.0, -0.5, 2.0, -1.0, 0.5])
    assert s["n"] == 5
    assert 0.0 <= s["win_rate"] <= 1.0


def test_record_fill_from_execution_skips_when_layer_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCTION_HARDENING_LAYER", "0")
    out = record_fill_from_execution(
        venue="coinbase",
        order_id="o1",
        client_order_id="c1",
        product_or_market="BTC-USD",
        side="buy",
        filled_base=0.001,
        fill_price=100000.0,
        fee_usd=0.01,
    )
    assert out is None


@patch("trading_ai.shark.production_hardening.double_entry.layer_enabled", return_value=True)
@patch("trading_ai.shark.production_hardening.double_entry._append_jsonl")
def test_append_trade_ledger_persists(mock_append: MagicMock, _le: MagicMock) -> None:
    lines = [
        {"leg": "debit", "account": "usd", "usd_equiv": 50.0},
        {"leg": "credit", "account": "contracts", "usd_equiv": 50.0},
    ]
    rid = append_trade_ledger(
        venue="kalshi",
        order_id="oid",
        client_order_id="cid",
        product_or_market="KXTEST",
        lines=lines,
    )
    assert rid
    assert mock_append.called
