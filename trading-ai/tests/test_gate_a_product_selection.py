"""Gate A selection policy + explicit path (no network when explicit product is allowlisted)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_explicit_product_writes_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import run_gate_a_product_selection
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    out = run_gate_a_product_selection(
        runtime_root=tmp_path,
        client=CoinbaseClient(),
        quote_usd=10.0,
        explicit_product_id="BTC-USD",
    )
    assert out.get("selected_product") == "BTC-USD"
    assert out.get("selected_product_source") == "operator_explicit"
    snap = tmp_path / "data" / "control" / "gate_a_selection_snapshot.json"
    assert snap.is_file()


def test_priority_policy_default_has_btc_eth_first() -> None:
    from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import load_gate_a_product_policy

    p = load_gate_a_product_policy(runtime_root=Path("."))
    pri = p.get("priority_products") or []
    assert pri[:2] == ["BTC-USD", "ETH-USD"]


def test_autonomous_gate_a_selection_not_btc_only_when_conservative_universe_allows_sol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression: autonomous mode uses anchored_majors_only=True, but that must not mean BTC/ETH-only.
    It should allow other approved major bases (e.g. SOL) while still remaining conservative.
    """
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)

    # Make SOL the best by spread, and ensure quote preflight aligns chosen==preferred.
    def _fake_public_req(path: str) -> dict:
        # /market/products/{pid}/ticker
        pid = path.split("/market/products/", 1)[-1].split("/ticker", 1)[0].upper()
        if pid == "BTC-USD":
            return {"bid": "100.0", "ask": "100.6", "price": "100.3"}  # ~59.8 bps (fail default 50)
        if pid == "ETH-USD":
            return {"bid": "100.0", "ask": "100.4", "price": "100.2"}  # ~39.9 bps
        if pid == "SOL-USD":
            return {"bid": "100.0", "ask": "100.1", "price": "100.05"}  # ~10 bps (best)
        return {"bid": "100.0", "ask": "100.9", "price": "100.45"}

    monkeypatch.setattr(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        _fake_public_req,
    )

    def _fake_exact_preflight(
        _client: object,
        *,
        product_id: str,
        quote_notional: float,
        runtime_root: Path,
    ):
        return True, {"product_id": product_id, "quote_notional": quote_notional, "runtime_root": str(runtime_root)}, None

    monkeypatch.setattr(
        "trading_ai.runtime_proof.coinbase_accounts.preflight_exact_spot_product",
        _fake_exact_preflight,
    )

    from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import run_gate_a_product_selection
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    out = run_gate_a_product_selection(
        runtime_root=tmp_path,
        client=CoinbaseClient(),
        quote_usd=10.0,
        explicit_product_id=None,
        anchored_majors_only=True,
    )
    assert out.get("selected_product") == "SOL-USD"
    assert out.get("selected_product_source") == "gate_a_selection_engine"


def test_capital_split_fail_closed_without_deployable() -> None:
    from trading_ai.orchestration.coinbase_gate_selection.coinbase_capital_split import compute_coinbase_gate_capital_split

    out = compute_coinbase_gate_capital_split(None, runtime_root=Path("."))
    assert out.get("ok") is False
