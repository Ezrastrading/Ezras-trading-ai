"""Gate A universe, capital split env, Gate B monitor zone, specialist seed (no live authority)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trading_ai.global_layer.canonical_specialist_seed import ensure_avenue_a_all_specialists, ensure_gate_b_specialists
from trading_ai.shark.coinbase_spot.capital_allocation import (
    compute_gate_allocation_split,
    idle_loan_unused_gate_quota_to_other_allowed,
)
from trading_ai.shark.coinbase_spot.gate_a_universe import build_gate_a_universe_artifact, evaluate_gate_a_row, rank_gate_a_candidates
from trading_ai.shark.coinbase_spot.gate_b_monitor import GateBMonitorState, gate_b_monitor_tick


def test_gate_a_rejects_wide_spread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_A_MAX_SPREAD_BPS", "20")
    r = evaluate_gate_a_row(
        {
            "product_id": "ZZZ-USD",
            "liquidity_score": 0.9,
            "spread_bps": 80.0,
            "quote_volume_24h_usd": 10_000_000.0,
            "volatility_bps": 10.0,
        },
    )
    assert r.accepted is False
    assert "spread_above_max" in r.reject_reasons


def test_gate_a_ranks_primary_header_higher() -> None:
    rows = [
        {
            "product_id": "SOL-USD",
            "liquidity_score": 0.8,
            "spread_bps": 10.0,
            "quote_volume_24h_usd": 9_000_000.0,
            "volatility_bps": 10.0,
        },
        {
            "product_id": "BTC-USD",
            "liquidity_score": 0.75,
            "spread_bps": 10.0,
            "quote_volume_24h_usd": 9_000_000.0,
            "volatility_bps": 10.0,
        },
    ]
    ranked = rank_gate_a_candidates(rows)
    assert ranked[0].product_id == "BTC-USD"


def test_gate_a_universe_artifact_lists_rejections() -> None:
    art = build_gate_a_universe_artifact(
        all_rows=[
            {"product_id": "BAD-USD", "liquidity_score": 0.1, "spread_bps": 5.0, "quote_volume_24h_usd": 100.0, "volatility_bps": 5.0},
            {"product_id": "BTC-USD", "liquidity_score": 0.8, "spread_bps": 8.0, "quote_volume_24h_usd": 9_000_000.0, "volatility_bps": 10.0},
        ],
        chosen_product_id="BTC-USD",
    )
    assert art["accepted_count"] >= 1
    assert any("BAD-USD" == x["product_id"] for x in art["rejected"])


def test_capital_split_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVENUE_A_GATE_A_QUOTE_SHARE", "0.6")
    monkeypatch.setenv("AVENUE_A_GATE_B_QUOTE_SHARE", "0.4")
    s = compute_gate_allocation_split()
    assert abs(s.gate_a - 0.6) < 1e-6
    assert abs(s.gate_b - 0.4) < 1e-6
    monkeypatch.delenv("AVENUE_A_GATE_A_QUOTE_SHARE", raising=False)
    monkeypatch.delenv("AVENUE_A_GATE_B_QUOTE_SHARE", raising=False)


def test_idle_loan_default_off() -> None:
    assert idle_loan_unused_gate_quota_to_other_allowed() is False


def test_gate_b_zone_ceiling_exit() -> None:
    st = GateBMonitorState(
        product_id="X-USD",
        entry_price=100.0,
        peak_price=100.0,
        entry_ts=0.0,
        last_price=115.0,
    )
    r = gate_b_monitor_tick(
        st,
        now_ts=10.0,
        profit_zone_min_pct=0.10,
        profit_zone_max_pct=0.12,
        trailing_stop_from_peak_pct=0.03,
        max_hold_sec=3600.0,
    )
    assert r["exit"] is True
    assert r["exit_reason"] == "profit_zone_ceiling"


def test_specialist_seed_no_live_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = tmp_path / "r.json"
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("EZRAS_BOT_SPAWN_COOLDOWN_SEC", "0")
    g = tmp_path / "gov"
    g.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("trading_ai.global_layer._bot_paths.global_layer_governance_dir", lambda: g)
    monkeypatch.setattr("trading_ai.global_layer.orchestration_paths.global_layer_governance_dir", lambda: g)
    (g / "orchestration_kill_switch.json").write_text(
        '{"truth_version":"orchestration_kill_switch_v1","orchestration_frozen":false,"avenue":{},"gate":{},"bot_class":{},"bot_id":{}}',
        encoding="utf-8",
    )
    out = ensure_avenue_a_all_specialists(registry_path=reg)
    assert out.get("ok") is True
    from trading_ai.global_layer.bot_registry import get_bot
    from trading_ai.global_layer.orchestration_schema import PermissionLevel

    b = get_bot("ezras_spec_A_gate_b_gainer_scan", path=reg)
    assert b is not None
    assert str(b.get("permission_level")) != PermissionLevel.EXECUTION_AUTHORITY.value
