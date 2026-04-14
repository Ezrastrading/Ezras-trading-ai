"""CEO autonomous session layer — prompts, persistence, parameters, Telegram."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from trading_ai.shark.models import HuntType, MarketSnapshot, OpportunityTier, ScoredOpportunity


def test_ceo_parse_and_normalize_json():
    from trading_ai.shark import ceo_sessions

    raw = '{"assessment":"ok","working":["a"],"failing":[],"new_strategies":[],"parameter_changes":{"hunt_type_adjustments":{"pure_arbitrage":1.1},"min_edge_changes":{"crypto_scalp":0.05}},"next_session_target":"x","confidence":0.7}'
    d = ceo_sessions._parse_ceo_json_response(f"prefix text {raw} suffix")
    n = ceo_sessions._normalize_ceo_result(d)
    assert n["assessment"] == "ok"
    assert n["parameter_changes"]["min_edge_changes"]["crypto_scalp"] == 0.05


def test_ceo_session_history_append(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import ceo_sessions

    ceo_sessions.save_session_history({"session_type": "MORNING", "assessment": "prior"})
    h = ceo_sessions.load_session_history()
    assert len(h) == 1
    assert h[0]["session_type"] == "MORNING"


def test_ceo_apply_parameter_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import ceo_sessions
    from trading_ai.shark.state import BAYES

    BAYES.hunt_weights["pure_arbitrage"] = 1.0
    ceo_sessions.CEO_EDGE_OVERRIDES.clear()
    changes = {
        "hunt_type_adjustments": {"pure_arbitrage": 1.5},
        "min_edge_changes": {"crypto_scalp": 0.08},
    }
    with patch("trading_ai.shark.state_store.save_bayesian_snapshot", lambda: None):
        ceo_sessions.apply_ceo_parameter_changes(changes)
    assert BAYES.hunt_weights["pure_arbitrage"] == pytest.approx(1.5)
    assert ceo_sessions.CEO_EDGE_OVERRIDES["crypto_scalp"] == 0.08
    ov = json.loads((tmp_path / "shark" / "state" / "ceo_overrides.json").read_text(encoding="utf-8"))
    assert ov["min_edge_changes"]["crypto_scalp"] == 0.08


def test_ceo_telegram_body_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import ceo_sessions

    sent = []

    def cap(msg: str) -> bool:
        sent.append(msg)
        return True

    with patch("trading_ai.shark.reporting.send_telegram", cap):
        ceo_sessions.send_ceo_session_telegram(
            "MIDDAY",
            {
                "assessment": "Desk flat.",
                "working": ["arb"],
                "failing": [],
                "new_strategies": [{"name": "X", "description": "d", "priority": "high"}],
                "next_session_target": "More edge",
            },
            {"net_worth": 100.0, "pnl_today": 1.5},
        )
    assert sent and "CEO BRIEFING" in sent[0] and "MIDDAY" in sent[0] and "Desk flat." in sent[0]


def test_ceo_run_session_mocks_pipeline_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from trading_ai.shark import ceo_sessions
    from trading_ai.shark.state_store import CapitalRecord, save_capital, save_positions

    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0, starting_capital=100.0))
    save_positions(
        {
            "open_positions": [],
            "history": [
                {
                    "pnl": 2.0,
                    "closed_at": 1e9,
                    "hunt_types": [HuntType.PURE_ARBITRAGE.value],
                    "market_category": "sports",
                }
            ],
        }
    )

    payload = json.dumps(
        {
            "assessment": "Solid.",
            "working": ["w1"],
            "failing": [],
            "new_strategies": [
                {
                    "name": "Gamma scalp",
                    "description": "test idea",
                    "implementation": "code it",
                    "expected_edge": 0.02,
                    "priority": "high",
                }
            ],
            "parameter_changes": {"hunt_type_adjustments": {}, "min_edge_changes": {}},
            "next_session_target": "Scale",
            "confidence": 0.6,
        }
    )

    class _Block:
        text = payload

    class _Resp:
        content = [_Block()]

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _Resp()

    with patch("trading_ai.shark.ceo_sessions._get_anthropic_client", return_value=fake_client), patch(
        "trading_ai.shark.ceo_sessions.send_ceo_session_telegram", lambda *a, **k: None
    ), patch("trading_ai.shark.state_store.save_bayesian_snapshot", lambda: None):
        out = ceo_sessions.run_ceo_session("EOD")

    assert out["assessment"] == "Solid."
    hist = ceo_sessions.load_session_history()
    assert hist[-1]["session_type"] == "EOD"
    pipe = ceo_sessions.load_strategy_pipeline()
    assert any(p.get("name") == "Gamma scalp" and p.get("status") == "proposed" for p in pipe)


def test_ceo_min_edge_floor_in_executor():
    from trading_ai.shark import ceo_sessions
    from trading_ai.shark.executor import build_execution_intent
    from trading_ai.shark.models import HuntSignal

    ceo_sessions.CEO_EDGE_OVERRIDES["pure_arbitrage"] = 0.25
    m = MarketSnapshot(
        market_id="m1",
        outlet="kalshi",
        yes_price=0.48,
        no_price=0.50,
        volume_24h=5000.0,
        time_to_resolution_seconds=86400.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="default",
    )
    hunts = [HuntSignal(HuntType.PURE_ARBITRAGE, 0.15, 0.8, {})]
    sc = ScoredOpportunity(
        market=m,
        hunts=hunts,
        score=10.0,
        tier=OpportunityTier.TIER_A,
        edge_size=0.20,
        confidence=0.8,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        tier_sizing_multiplier=1.0,
    )
    intent = build_execution_intent(
        sc,
        capital=500.0,
        outlet="kalshi",
        min_edge_effective=0.01,
    )
    assert intent is None
    ceo_sessions.CEO_EDGE_OVERRIDES.pop("pure_arbitrage", None)


def test_ceo_bump_scan_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark import ceo_sessions

    day = ceo_sessions._now_et().strftime("%Y-%m-%d")
    ceo_sessions.bump_daily_scan_stats(10, 2)
    raw = json.loads(ceo_sessions.ceo_scan_day_path().read_text(encoding="utf-8"))
    assert raw["day"] == day
    assert raw["markets_scanned"] == 10
    assert raw["execution_attempts"] == 2
