"""Shark system tests — gates 1–42 + integration."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_ai.governance import system_doctrine as doctrine
from trading_ai.governance.storage_architecture import append_shark_audit_record
from trading_ai.shark.capital_phase import detect_phase, phase_params
from trading_ai.shark.execution import hook_post_trade_resolution, run_execution_chain, trigger_bayesian_after_resolution
from trading_ai.shark.executor import build_execution_intent
from trading_ai.shark.gap_hunter import (
    GapExploitationState,
    confirm_gap_with_win_rate,
    confirm_pattern,
    gap_closure_triggers,
    gap_exploitation_scan_interval,
    gap_score,
    should_escalate,
)
from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
from trading_ai.shark.models import (
    ExecutionIntent,
    GapObservation,
    HuntSignal,
    HuntType,
    MarketSnapshot,
    OpenPosition,
    OpportunityTier,
    OrderResult,
)
from trading_ai.shark.reporting import alert_gap_detected, alert_trade_fired, clear_test_alerts, last_alerts_for_tests
from trading_ai.shark.risk_context import build_risk_context, effective_min_edge
from trading_ai.shark.scorer import score_opportunity
from trading_ai.shark.state import BAYES, HOT, LOSS_TRACKER, MANDATE
from trading_ai.shark.state_store import (
    CapitalRecord,
    apply_win_loss_to_capital,
    capital_path,
    load_capital,
    save_capital,
    save_execution_control,
)
from trading_ai.shark.cli import sample_outputs_for_docs
from trading_ai.shark.scanner import OutletRegistry, resolve_scan_interval_seconds


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    MANDATE.compounding_paused = False
    MANDATE.gaps_paused = False
    MANDATE.execution_paused = False
    LOSS_TRACKER._streak.clear()
    BAYES.strategy_weights.clear()
    BAYES.strategy_weights["default"] = 0.5
    BAYES.hunt_weights.clear()
    for h in HuntType:
        BAYES.hunt_weights[h.value] = 0.5
    BAYES.outlet_weights.clear()
    BAYES.hour_edge_quality.clear()
    BAYES.trade_count = 0
    clear_test_alerts()
    yield


def test_1_hunt_structural_arbitrage():
    m = MarketSnapshot(
        market_id="a",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = run_hunts_on_market(m)
    types = {h.hunt_type for h in hs}
    assert HuntType.STRUCTURAL_ARBITRAGE in types


def test_2_hunt_dead_market_convergence():
    m = MarketSnapshot(
        market_id="b",
        outlet="poly",
        yes_price=0.80,
        no_price=0.19,
        volume_24h=2000.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        underlying_data_if_available={"model_prob": 0.94},
    )
    hs = run_hunts_on_market(m)
    assert any(h.hunt_type == HuntType.DEAD_MARKET_CONVERGENCE for h in hs)


def test_3_cross_platform_mispricing():
    g = "evt-x"
    m1 = MarketSnapshot(
        market_id="m1",
        outlet="polymarket",
        yes_price=0.40,
        no_price=0.62,
        volume_24h=2000.0,
        time_to_resolution_seconds=5000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        canonical_event_key=g,
    )
    m2 = MarketSnapshot(
        market_id="m2",
        outlet="kalshi",
        yes_price=0.52,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=5000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        canonical_event_key=g,
    )
    ctx = group_markets_by_event([m1, m2])
    hs1 = run_hunts_on_market(m1, cross_context=ctx)
    assert any(h.hunt_type == HuntType.CROSS_PLATFORM_MISPRICING for h in hs1)


def test_4_scorer_tiers():
    base = MarketSnapshot(
        market_id="c",
        outlet="o",
        yes_price=0.45,
        no_price=0.51,
        volume_24h=50000.0,
        time_to_resolution_seconds=7200.0,
        resolution_criteria="r",
        last_price_update_timestamp=0.0,
        required_position_dollars=100.0,
    )
    h1 = run_hunts_on_market(base)[0]
    from trading_ai.shark.models import HuntSignal

    dual = score_opportunity(
        base,
        [h1, HuntSignal(HuntType.CROSS_PLATFORM_MISPRICING, 0.06, 0.6, {})],
        strategy_key="t",
    )
    assert dual.tier == OpportunityTier.TIER_A

    big = MarketSnapshot(
        market_id="d",
        outlet="o",
        yes_price=0.40,
        no_price=0.55,
        volume_24h=500000.0,
        time_to_resolution_seconds=1900.0,
        resolution_criteria="r",
        last_price_update_timestamp=0.0,
        required_position_dollars=100.0,
    )
    hx = run_hunts_on_market(big)[0]
    one = score_opportunity(big, [hx], strategy_key="t")
    assert one.tier in (OpportunityTier.TIER_B, OpportunityTier.TIER_C, OpportunityTier.BELOW_THRESHOLD)


def test_5_kelly_respects_phase_limits():
    m = MarketSnapshot(
        market_id="e",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="cat",
    )
    hs = run_hunts_on_market(m)
    from trading_ai.shark.models import HuntSignal

    sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {"x": 1})], strategy_key="t")
    intent = build_execution_intent(sc, capital=50.0, outlet="kalshi", market_category="cat")
    assert intent is not None
    assert intent.stake_fraction_of_capital <= 0.16 + 1e-6


def test_6_anti_forced_trade_blocks():
    ctx = doctrine.DoctrineContext(
        source="shark_compounding",
        edge_after_fees=0.01,
        min_edge_for_phase=0.07,
        anti_forced_trade=True,
    )
    r = doctrine.check_doctrine_gate(ctx)
    assert not r.ok


def test_7_loss_cluster_reduces_sizing():
    ht = HuntType.STRUCTURAL_ARBITRAGE
    for _ in range(3):
        LOSS_TRACKER.record_outcome(
            strategy="s",
            hunt_type=ht,
            outlet="o",
            market_category="mc",
            win=False,
        )
    m = LOSS_TRACKER.cluster_multiplier(strategy="s", hunt_type=ht, outlet="o", market_category="mc")
    assert m == 0.5


def test_8_gap_oracle_lag_pattern():
    obs = [
        GapObservation("oracle_lag", 280.0, 1.0, 200000.0, 0.22, "none")
        for _ in range(6)
    ]
    assert gap_score(obs) > 0.0
    ok, sc = should_escalate(obs)
    assert ok and sc > 0.75


def test_9_gap_exploitation_mode_on_score():
    obs = [
        GapObservation("oracle_lag", 80.0, 0.95, 20000.0, 0.18, "none")
        for _ in range(5)
    ]
    assert confirm_pattern(obs, min_obs=5)
    st = GapExploitationState(active=True)
    assert gap_exploitation_scan_interval(st) == 30.0


def test_10_gap_closure_detection():
    trades = [True] * 9 + [False] * 4
    recent = [False] * 10
    hit, _ = gap_closure_triggers(recent_trades=recent, baseline_lag=100.0, current_lag=30.0, competition="heavy")
    assert hit
    hit2, _ = gap_closure_triggers(
        recent_trades=trades,
        baseline_lag=100.0,
        current_lag=35.0,
        competition="none",
    )
    assert hit2


def test_11_execution_chain_logs_steps():
    m = MarketSnapshot(
        market_id="f",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="c1",
    )
    hs = run_hunts_on_market(m)
    from trading_ai.shark.models import HuntSignal

    sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})], strategy_key="t")
    res = run_execution_chain(
        sc, capital=80.0, outlet="kalshi", estimated_execution_delay_seconds=1.0, execute_live=False
    )
    assert res.ok
    steps = [a["step"] for a in res.audit]
    assert "1_doctrine_gate" in steps and "15_telegram_resolution" in steps
    assert "5b_claude" in steps


def test_12_telegram_reporting_trade():
    alert_trade_fired(hunt_types=["structural_arbitrage"], edge=0.08, position_fraction=0.1, capital=100.0)
    assert any(x["kind"] == "trade_fired" for x in last_alerts_for_tests())


def test_13_phase_transition_updates_params():
    assert detect_phase(50.0).value == "phase_1"
    assert detect_phase(150.0).value == "phase_2"
    assert detect_phase(600.0).value == "phase_3"
    p1 = phase_params(detect_phase(50.0))
    p5 = phase_params(detect_phase(30000.0))
    # Phase 1 bootstrap uses a temporarily lower min_edge (<$100); phases 2+ tighten.
    assert p1.min_edge == 0.02
    assert p5.min_edge == 0.05
    assert p1.max_single_position_fraction > p5.max_single_position_fraction


def test_14_doctrine_blocks_noncompliant():
    save_execution_control({"manual_pause": True})
    try:
        m = MarketSnapshot(
            market_id="g",
            outlet="kalshi",
            yes_price=0.45,
            no_price=0.50,
            volume_24h=2000.0,
            time_to_resolution_seconds=4000.0,
            resolution_criteria="test",
            last_price_update_timestamp=0.0,
        )
        hs = run_hunts_on_market(m)
        from trading_ai.shark.models import HuntSignal

        sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})], strategy_key="t")
        res = run_execution_chain(sc, capital=80.0, outlet="kalshi")
        assert not res.ok
    finally:
        save_execution_control({"manual_pause": False})


def test_15_bayesian_update_shifts_weights():
    for i in range(16):
        trigger_bayesian_after_resolution(
            strategy="alpha",
            hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
            outlet="kalshi",
            win=(i % 2 == 0),
        )
    w = BAYES.strategy_weights.get("alpha", 0.5)
    assert w != 0.5


def test_16_scan_same_at_3am_and_3pm_no_clock_gate():
    t_am = 1000000000.0
    t_pm = t_am + 12 * 3600
    assert resolve_scan_interval_seconds(now=t_am, gap_exploitation_active=False) == resolve_scan_interval_seconds(
        now=t_pm, gap_exploitation_active=False
    )


def test_17_scan_interval_independent_of_hour():
    base = 1_700_000_000
    assert resolve_scan_interval_seconds(now=float(base), gap_exploitation_active=False) == resolve_scan_interval_seconds(
        now=float(base + 5 * 3600), gap_exploitation_active=False
    )


def test_18_hot_burst_triggers_90s_mode():
    now = time.time()
    HOT.record_opportunity(now - 200)
    HOT.record_opportunity(now - 100)
    HOT.record_opportunity(now)
    assert HOT.is_hot(now)
    assert resolve_scan_interval_seconds(now=now, gap_exploitation_active=False) < 120


def test_19_capital_persists_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = capital_path()
    rec = CapitalRecord(current_capital=77.0, peak_capital=77.0, starting_capital=50.0)
    save_capital(rec)
    assert p.is_file()
    rec2 = load_capital()
    assert rec2.current_capital == 77.0


def test_20_phase_transition_100():
    assert detect_phase(99.0).value == "phase_1"
    assert detect_phase(100.0).value == "phase_2"


def test_21_phase_transition_500():
    assert detect_phase(499.0).value == "phase_2"
    assert detect_phase(500.0).value == "phase_3"


def test_22_outlet_fallback_when_one_down():
    class Bad:
        outlet_name = "bad"

        def fetch_binary_markets(self):
            raise RuntimeError("down")

    class Ok:
        outlet_name = "ok"

        def fetch_binary_markets(self):
            return [
                MarketSnapshot(
                    market_id="x",
                    outlet="ok",
                    yes_price=0.5,
                    no_price=0.5,
                    volume_24h=1000.0,
                    time_to_resolution_seconds=3600.0,
                    resolution_criteria="r",
                    last_price_update_timestamp=0.0,
                )
            ]

    reg = OutletRegistry()
    reg.register(Bad())
    reg.register(Ok())
    rows = reg.scan_all()
    assert len(rows) == 1
    assert reg.last_health["bad"].startswith("error")


def test_23_win_updates_capital(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0, starting_capital=50.0))
    apply_win_loss_to_capital(10.0)
    assert load_capital().current_capital == 110.0


def test_24_loss_updates_capital(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0, starting_capital=50.0))
    apply_win_loss_to_capital(-15.0)
    assert load_capital().current_capital == 85.0


def test_25_gap_found_alert_and_exploitation():
    obs = [GapObservation("oracle_lag", 280.0, 1.0, 200000.0, 0.22, "none") for _ in range(5)]
    wins = [True, True, True, True, True]
    assert confirm_gap_with_win_rate(obs, wins)
    alert_gap_detected(
        gap_type="oracle_lag",
        score=gap_score(obs),
        edge=0.2,
        volume=200000.0,
        window_duration="hours",
        recommended_allocation=100.0,
    )
    assert any(x["kind"] == "gap_detected" for x in last_alerts_for_tests())


def test_26_gap_closure_returns_standard():
    st = GapExploitationState(active=True)
    st.active = False
    assert gap_exploitation_scan_interval(st) > 30.0


def test_27_drawdown_25_reduces_sizing():
    r = build_risk_context(
        current_capital=70.0,
        peak_capital=100.0,
        base_min_edge=0.07,
        last_trade_unix=time.time(),
        now_unix=time.time(),
    )
    assert r.drawdown_over_25pct
    assert r.position_size_multiplier == 0.5


def test_27b_zero_trades_resets_stale_peak_capital(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("KALSHI_ACTUAL_BALANCE", raising=False)
    save_capital(
        CapitalRecord(
            current_capital=52.70,
            peak_capital=100.0,
            starting_capital=52.70,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
        )
    )
    MANDATE.execution_paused = True
    rec = load_capital()
    assert rec.peak_capital == pytest.approx(52.70, rel=1e-6)
    assert doctrine.is_execution_paused() is False


def test_28_drawdown_40_pauses_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(
        CapitalRecord(
            current_capital=50.0,
            peak_capital=100.0,
            starting_capital=50.0,
            total_trades=5,
            winning_trades=2,
            losing_trades=3,
        )
    )
    MANDATE.execution_paused = False
    load_capital()
    assert MANDATE.execution_paused is True
    assert doctrine.is_execution_paused() is True


def test_29_idle_widen_max_15pct():
    base = 0.07
    em = effective_min_edge(base, idle_capital_widen=True, drawdown_over_25pct=False)
    assert abs(em - base * 1.15) < 1e-9


def test_30_forced_trade_blocked_always():
    ctx = doctrine.DoctrineContext(
        source="shark_compounding",
        edge_after_fees=0.04,
        min_edge_for_phase=0.07,
        anti_forced_trade=True,
    )
    assert not doctrine.check_doctrine_gate(ctx).ok


def test_sample_docs_outputs():
    s = sample_outputs_for_docs()
    assert "STRUCTURAL GAP DETECTED" in s["gap_alert"]
    assert "Ezras Shark System" in s["startup"]
    assert s["gap_score_sample"] > 0


def test_audit_trail_append(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    append_shark_audit_record({"test": True})
    p = Path(os.environ["EZRAS_RUNTIME_ROOT"]) / "shark" / "logs" / "shark_audit.jsonl"
    assert p.is_file()
    assert json.loads(p.read_text().strip())["test"] is True


def _load_setup_env():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("setup_env", root / "setup_env.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_31_kalshi_fetcher_maps_response_to_market_snapshot():
    from trading_ai.shark.outlets.kalshi import map_kalshi_market_to_snapshot

    now = 1_700_000_000.0
    m = {
        "ticker": "KXTEST-1",
        "yes_ask": 47,
        "no_ask": 53,
        "volume_24h": 1234.0,
        "title": "Test market title",
        "close_time": now + 7200.0,
    }
    sn = map_kalshi_market_to_snapshot(m, now)
    assert sn.market_id == "KXTEST-1"
    assert sn.outlet == "kalshi"
    assert abs(sn.yes_price - 0.47) < 1e-9
    assert abs(sn.no_price - 0.53) < 1e-9
    assert sn.volume_24h == 1234.0
    assert sn.resolution_criteria == "Test market title"
    assert sn.time_to_resolution_seconds >= 7100.0
    assert sn.last_price_update_timestamp == now


def test_31b_kalshi_tradeable_accepts_non_open_status():
    from trading_ai.shark.outlets.kalshi import _kalshi_market_tradeable

    now = 1_700_000_000.0
    m = {
        "ticker": "KX-1",
        "status": "active",
        "yes_ask": 50,
        "no_ask": 50,
        "close_time": now + 3600.0,
        "volume": 50.0,
    }
    assert _kalshi_market_tradeable(m, now)
    assert not _kalshi_market_tradeable({**m, "status": "closed"}, now)
    assert not _kalshi_market_tradeable({**m, "settled": True}, now)


def test_31b2_kalshi_tradeable_rejects_parlays_and_low_volume():
    from trading_ai.shark.outlets.kalshi import _kalshi_market_tradeable

    now = 1_700_000_000.0
    base = {
        "status": "active",
        "yes_ask": 50,
        "no_ask": 50,
        "close_time": now + 3600.0,
        "volume": 50.0,
    }
    assert not _kalshi_market_tradeable({**base, "ticker": "KXMVEPARLAY-1"}, now)
    assert not _kalshi_market_tradeable({**base, "ticker": "KXMVSPORT-1"}, now)
    assert not _kalshi_market_tradeable({**base, "ticker": "KXMVCROSS-1"}, now)
    assert not _kalshi_market_tradeable({**base, "ticker": "KXMVOTHER-1"}, now)
    assert not _kalshi_market_tradeable(
        {
            **base,
            "ticker": "KX-2",
            "close_time": now + 10 * 3600,
            "volume": 0,
            "volume_24h": 0,
            "open_interest": 0,
        },
        now,
    )
    assert not _kalshi_market_tradeable(
        {**base, "ticker": "KX-2b", "close_time": now + 3600, "volume": 0, "volume_24h": 0, "open_interest": 0},
        now,
    )
    assert _kalshi_market_tradeable(
        {**base, "ticker": "KX-3", "volume": 0, "volume_24h": 0, "open_interest": 2.0}, now
    )


def test_31c_gamma_row_normalization_tradeable():
    from trading_ai.shark.outlets import polymarket as poly

    now = 1_700_000_000.0
    g = {
        "conditionId": "0xabc",
        "question": "Q?",
        "endDate": "2030-01-01T12:00:00Z",
        "clobTokenIds": '["111", "222"]',
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.6", "0.4"]',
        "volume24hr": 99.0,
    }
    row = poly._gamma_market_to_clob_like_row(g)
    assert row["condition_id"] == "0xabc"
    assert len(row["tokens"]) == 2
    assert poly._is_tradeable_market_dict(row, now)


def test_32b_kalshi_rsa_pem_normalization_and_signed_headers(monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("KALSHI_API_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setenv("KALSHI_ACCESS_KEY_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.delenv("KALSHI_API_BASE", raising=False)

    from trading_ai.shark.outlets import kalshi as k

    assert k.normalize_kalshi_key_material(os.environ["KALSHI_API_KEY"]).startswith("-----BEGIN")
    assert k.path_for_kalshi_signature("https://api.kalshi.com/trade-api/v2/markets?limit=5") == "/trade-api/v2/markets"

    h = k.build_kalshi_request_headers("GET", "https://trading-api.kalshi.com/trade-api/v2/exchange/status")
    assert h.get("KALSHI-ACCESS-KEY") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert h.get("KALSHI-ACCESS-TIMESTAMP")
    assert h.get("KALSHI-ACCESS-SIGNATURE")
    assert "Authorization" not in h


def test_32_kalshi_order_placement_returns_valid_order_result_structure(monkeypatch):
    from trading_ai.shark.outlets.kalshi import KalshiClient

    def fake_request(self, method, path, **kwargs):
        assert method == "POST"
        assert "/portfolio/orders" in path
        return {
            "order": {"order_id": "ord-k-1", "status": "filled"},
            "filled_count": 10,
            "filled_price": 4700,
        }

    monkeypatch.setattr(KalshiClient, "_request", fake_request)
    c = KalshiClient(api_key="test-key")
    r = c.place_order(ticker="KX-T", side="yes", count=10, yes_price_cents=47)
    assert r.order_id == "ord-k-1"
    assert r.outlet == "kalshi"
    assert r.status
    assert isinstance(r.timestamp, float)
    assert r.filled_size == 10.0
    assert abs(r.filled_price - 47.0) < 1e-9


def test_33_polymarket_order_signing_produces_valid_eip712_signature():
    pytest.importorskip("eth_account")
    from eth_account import Account

    from trading_ai.shark.polymarket_live import sign_polymarket_order_eip712

    acct = Account.create()
    pk_hex = acct.key.hex()
    if not pk_hex.startswith("0x"):
        pk_hex = "0x" + pk_hex
    sig = sign_polymarket_order_eip712(
        private_key_hex=pk_hex,
        maker=acct.address,
        token_id="999",
        maker_amount=1_000_000,
        taker_amount=5,
        side_buy=True,
    )
    assert isinstance(sig, str)
    assert sig.startswith("0x")
    assert len(sig) >= 130


def test_34_manifold_bet_placement_returns_valid_order_result(monkeypatch):
    from trading_ai.shark import manifold_live as ml
    from trading_ai.shark.models import ExecutionIntent

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"betId": "bet-xyz-1", "status": "ok"}'

    monkeypatch.setenv("MANIFOLD_API_KEY", "mk-test")
    monkeypatch.setattr(ml.urllib.request, "urlopen", lambda req, timeout=None: FakeResp())

    intent = ExecutionIntent(
        market_id="manifold:cid-42",
        outlet="manifold",
        side="yes",
        stake_fraction_of_capital=0.05,
        edge_after_fees=0.1,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        source="shark",
        expected_price=0.4,
        notional_usd=5.0,
        shares=10,
    )
    from trading_ai.shark.manifold_live import submit_manifold_bet

    r = submit_manifold_bet(intent)
    assert r.order_id == "bet-xyz-1"
    assert r.outlet == "manifold"
    assert r.status == "filled"
    assert isinstance(r.timestamp, float)


def test_35_high_slippage_flagged_but_trade_continues():
    from trading_ai.shark.execution_live import confirm_execution

    intent = ExecutionIntent(
        market_id="p1",
        outlet="polymarket",
        side="yes",
        stake_fraction_of_capital=0.05,
        edge_after_fees=0.10,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        source="shark",
        expected_price=0.50,
        notional_usd=5.0,
        shares=10,
    )
    order = OrderResult(
        order_id="o1",
        filled_price=0.90,
        filled_size=10.0,
        timestamp=time.time(),
        status="filled",
        outlet="polymarket",
    )
    conf = confirm_execution(order, intent)
    assert conf.high_slippage_warning is True
    assert conf.confirmed is True
    assert conf.unfilled_cancelled is False


def test_36_unfilled_order_cancelled_after_60_seconds():
    from trading_ai.shark.execution_live import confirm_execution

    clock = [0.0]

    def fake_now():
        return clock[0]

    def fake_sleep(delta):
        clock[0] += float(delta)

    cancelled = []

    def poll_order(oid):
        return {"status": "resting", "order": {"status": "resting"}}

    def cancel_order(oid):
        cancelled.append(oid)

    intent = ExecutionIntent(
        market_id="kalshi:KX-T",
        outlet="kalshi",
        side="yes",
        stake_fraction_of_capital=0.05,
        edge_after_fees=0.10,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        source="shark",
        expected_price=0.50,
        notional_usd=5.0,
        shares=10,
    )
    order = OrderResult(
        order_id="ord-rest",
        filled_price=0.50,
        filled_size=0.0,
        timestamp=time.time(),
        status="resting",
        outlet="kalshi",
    )
    conf = confirm_execution(
        order,
        intent,
        sleep_fn=fake_sleep,
        time_fn=fake_now,
        poll_order=poll_order,
        cancel_order=cancel_order,
    )
    assert conf.unfilled_cancelled is True
    assert conf.confirmed is False
    assert cancelled == ["ord-rest"]


def test_37_resolution_detected_and_capital_updated_correctly(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.execution_live import calculate_pnl, handle_resolution
    from trading_ai.shark.state_store import load_capital, load_positions, save_capital, save_positions

    save_capital(CapitalRecord(current_capital=50.0, peak_capital=50.0, starting_capital=50.0))
    save_positions({"open_positions": [], "history": []})

    pos = OpenPosition(
        position_id="pos-1",
        outlet="kalshi",
        market_id="KX-1",
        side="yes",
        entry_price=0.50,
        shares=10.0,
        notional_usd=10.0,
        order_id="o1",
        opened_at=time.time(),
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE.value],
        expected_edge=0.1,
    )
    pnl = calculate_pnl(pos, "YES")
    assert pnl > 0

    handle_resolution(
        pos,
        "YES",
        pnl,
        trade_id="trade-1",
        strategy_key="shark_default",
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        market_category="default",
    )
    rec = load_capital()
    assert rec.current_capital == 50.0 + pnl
    assert rec.total_trades == 1
    assert rec.winning_trades == 1
    data = load_positions()
    assert data["open_positions"] == []
    assert len(data["history"]) == 1
    assert data["history"][0]["pnl"] == pnl
    assert data["history"][0]["hunt_types"] == [HuntType.STRUCTURAL_ARBITRAGE.value]
    assert data["history"][0]["market_category"] == "default"


def test_38_telegram_send_succeeds_and_returns_true(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"ok":true}'

    from trading_ai.shark.reporting import send_telegram

    with patch("trading_ai.shark.reporting._requests.post", return_value=mock_resp):
        assert send_telegram("hello") is True


def test_39_telegram_failure_does_not_block_trade_execution(monkeypatch):
    from trading_ai.shark import execution as ex

    m = MarketSnapshot(
        market_id="fail-tg",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="c1",
    )
    hs = run_hunts_on_market(m)
    from trading_ai.shark.models import HuntSignal

    sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})], strategy_key="t")

    def boom(*a, **k):
        raise RuntimeError("submit failed")

    monkeypatch.setattr("trading_ai.shark.execution_live.submit_order", boom)

    res = run_execution_chain(sc, capital=80.0, outlet="kalshi", estimated_execution_delay_seconds=1.0, execute_live=True)
    assert res.ok is False
    assert res.halted_at == "submit_failed"


def test_40_setup_env_creates_all_required_directories(tmp_path):
    se = _load_setup_env()
    rt = tmp_path / "runtime"
    se.ensure_dirs(rt)
    assert (rt / "shark" / "state").is_dir()
    assert (rt / "shark" / "state" / "backups").is_dir()
    assert (rt / "shark" / "logs").is_dir()


def test_41_setup_env_initializes_capital_json_at_starting_capital(tmp_path, monkeypatch):
    se = _load_setup_env()
    rt = tmp_path / "runtime"
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(rt))
    monkeypatch.setenv("STARTING_CAPITAL", "25")
    se.ensure_dirs(rt)
    cap_path = rt / "shark" / "state" / "capital.json"
    assert not cap_path.exists()
    se.init_capital(rt)
    data = json.loads(cap_path.read_text(encoding="utf-8"))
    assert data["current_capital"] == 25.0
    assert data["starting_capital"] == 25.0
    assert data["phase"] == "phase_1"


def test_42_post_trade_hooks_fire_in_correct_order_after_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.execution_live import handle_resolution
    from trading_ai.shark.state_store import save_capital, save_positions

    order_calls = []

    save_capital(CapitalRecord(current_capital=50.0, peak_capital=50.0, starting_capital=50.0))
    save_positions({"open_positions": [], "history": []})

    pos = OpenPosition(
        position_id="pos-hook",
        outlet="kalshi",
        market_id="KX-2",
        side="yes",
        entry_price=0.50,
        shares=10.0,
        notional_usd=10.0,
        order_id="o2",
        opened_at=time.time(),
        hunt_types=[HuntType.DEAD_MARKET_CONVERGENCE.value],
        expected_edge=0.1,
    )

    def track_apply(pnl):
        order_calls.append("apply_capital")

    def track_hook(*args, **kwargs):
        order_calls.append("hook")

    def track_audit(rec):
        order_calls.append("audit")

    def track_tg(msg):
        order_calls.append("telegram")

    def track_dd():
        order_calls.append("drawdown")

    with patch("trading_ai.shark.state_store.apply_win_loss_to_capital", side_effect=track_apply), patch(
        "trading_ai.shark.execution.hook_post_trade_resolution", side_effect=track_hook
    ), patch("trading_ai.shark.execution_live.append_shark_audit_record", side_effect=track_audit), patch(
        "trading_ai.shark.reporting.send_telegram_trade_resolution", side_effect=track_tg
    ), patch("trading_ai.shark.execution_live.check_drawdown_after_resolution", side_effect=track_dd):
        handle_resolution(
            pos,
            "YES",
            5.0,
            trade_id="t-hook",
            strategy_key="shark_default",
            hunt_types=[HuntType.DEAD_MARKET_CONVERGENCE],
            market_category="default",
        )

    assert order_calls == ["apply_capital", "hook", "audit", "telegram", "drawdown"]


def test_61_all_required_env_keys_present_in_env_template():
    se = _load_setup_env()
    root = Path(__file__).resolve().parents[1]
    tmpl = (root / ".env.template").read_text(encoding="utf-8")
    keys_in_template = set()
    for line in tmpl.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" in s:
            keys_in_template.add(s.split("=", 1)[0].strip())
    for k in se.REQUIRED:
        assert k in keys_in_template, f"missing key in .env.template: {k}"


def test_62_missing_telegram_bot_token_raises_clear_environment_error(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    from trading_ai.shark.reporting import require_telegram_credentials

    with patch("trading_ai.shark.required_env.load_shark_dotenv", lambda: None):
        with pytest.raises(EnvironmentError) as exc:
            require_telegram_credentials()
    assert "TELEGRAM_BOT_TOKEN" in str(exc.value)


def test_63_missing_poly_wallet_key_raises_clear_environment_error(monkeypatch):
    monkeypatch.delenv("POLY_WALLET_KEY", raising=False)
    monkeypatch.setenv("POLY_API_KEY", "dummy-api")
    from trading_ai.shark.required_env import require_poly_wallet_key

    with patch("trading_ai.shark.required_env.load_shark_dotenv", lambda: None):
        with pytest.raises(EnvironmentError) as exc:
            require_poly_wallet_key()
    assert "POLY_WALLET_KEY" in str(exc.value)


def test_64_missing_ezras_runtime_root_gets_sensible_default(monkeypatch):
    monkeypatch.delenv("EZRAS_RUNTIME_ROOT", raising=False)
    from trading_ai.shark.state_store import require_ezras_runtime_root_configured

    with patch("trading_ai.shark.required_env.load_shark_dotenv", lambda: None):
        require_ezras_runtime_root_configured()
    root = os.environ.get("EZRAS_RUNTIME_ROOT", "")
    assert root
    if os.path.exists("/app"):
        assert root == "/app/ezras-runtime"
    else:
        assert root == str(Path.home() / "ezras-runtime")


def test_73_setup_env_exits_zero_with_empty_poly_keys(tmp_path, monkeypatch):
    """US / scan-only: POLY_WALLET_KEY and POLY_API_KEY may be empty."""
    se = _load_setup_env()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("STARTING_CAPITAL", "50")
    monkeypatch.setenv("KALSHI_API_KEY", "test-kalshi-key")
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-manifold-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("FRED_API_KEY", "test-fred")
    monkeypatch.delenv("POLY_WALLET_KEY", raising=False)
    monkeypatch.setenv("POLY_API_KEY", "")
    with patch.object(se, "test_polymarket", return_value=(True, "ok")), patch.object(
        se, "test_kalshi", return_value=(True, "ok")
    ), patch.object(se, "test_manifold", return_value=(True, "ok")), patch.object(se, "test_telegram", return_value=(True, "ok")), patch(
        "trading_ai.shark.outlets.polymarket.test_polymarket_credentials",
        return_value={
            "status_code": 200,
            "error": None,
            "balance": None,
            "key_id_used": "",
            "secret_set": False,
            "wallet_set": False,
        },
    ):
        assert se.main() == 0


def test_92_health_server_returns_200_on_health(tmp_path, monkeypatch):
    import urllib.request

    monkeypatch.setenv("PORT", "18888")
    from trading_ai.shark.health_server import start_health_server

    srv = start_health_server(18888)
    try:
        r = urllib.request.urlopen("http://127.0.0.1:18888/health", timeout=3)
        assert r.getcode() == 200
        body = json.loads(r.read().decode("utf-8"))
        assert body.get("status") == "alive"
    finally:
        srv.shutdown()


def test_93_recovery_flags_stale_scan_gap(tmp_path, monkeypatch):
    """Stale last_scan records offline gap; no Telegram on restart."""
    import time

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    from trading_ai.shark.recovery import last_scan_path, run_startup_recovery
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    save_capital(CapitalRecord(current_capital=50.0, peak_capital=50.0))
    last_scan_path().parent.mkdir(parents=True, exist_ok=True)
    old = time.time() - 700
    last_scan_path().write_text(json.dumps({"last_unix": old}), encoding="utf-8")

    from trading_ai.shark import recovery as rec_mod

    monkeypatch.setattr(rec_mod, "reconcile_open_positions", lambda: {"checked": 0, "resolved": 0})

    from trading_ai.shark.treasury import load_treasury, save_treasury

    st = load_treasury()
    st["kalshi_balance_usd"] = 10.50
    save_treasury(st)

    rep = run_startup_recovery(boot_unix=time.time())
    assert rep.get("restart_alert_sent") is False
    assert rep.get("offline_human") is not None
    assert rep.get("last_scan_age_seconds", 0) > 600


def test_94_supabase_push_pull_roundtrip(monkeypatch):
    import io

    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    store: dict[str, dict] = {}

    class Resp:
        def __init__(self, code: int = 200, body: str = "[]") -> None:
            self.status_code = code
            self.text = body

        def json(self):
            import json as _j

            return _j.loads(self.text)

    def fake_post(url, headers=None, json=None, timeout=30):
        k = json.get("key")
        store[k] = json
        return Resp(201, "[]")

    def fake_get(url, headers=None, timeout=30):
        return Resp(200, '[{"value": {"a": 1}}]')

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("requests.patch", lambda *a, **k: Resp(204, ""))

    from trading_ai.shark import remote_state as rs

    assert rs.push_state_to_supabase("capital", {"x": 1}) is True
    out = rs.pull_state_from_supabase("capital")
    assert out == {"a": 1}


def test_95_crash_recovery_no_telegram_restart_alert(tmp_path, monkeypatch):
    """Same as test_93 — stale scan recorded without restart Telegram."""
    test_93_recovery_flags_stale_scan_gap(tmp_path, monkeypatch)


def test_96_heartbeat_message_and_scheduler_heartbeat_job():
    import time

    from trading_ai.shark.reporting import format_shark_heartbeat_message, send_shark_heartbeat_alert

    msg = format_shark_heartbeat_message(
        uptime_hours=2.5,
        capital=100.0,
        trades_today=3,
        win_rate_pct=0.4,
        server_label="local",
        next_scan_seconds=300.0,
    )
    assert "SHARK ALIVE" in msg
    assert "40.0%" in msg

    from trading_ai.shark.scheduler import build_shark_scheduler

    seen = []

    def hb():
        seen.append(time.time())

    sched = build_shark_scheduler(
        standard_scan=lambda: None,
        hot_scan=lambda: None,
        gap_passive_scan=lambda: None,
        gap_active_scan=lambda: None,
        resolution_monitor=lambda: None,
        daily_memo=lambda: None,
        weekly_summary=lambda: None,
        state_backup=lambda: None,
        health_check=lambda: None,
        hot_window_active=lambda: False,
        gap_active=lambda: False,
        heartbeat=hb,
    )
    assert sched is not None
    ids = [j.id for j in sched.get_jobs()]
    assert "heartbeat" in ids


def test_91_scan_loop_calls_run_execution_chain_when_valid_opportunity(tmp_path, monkeypatch):
    """``run_scan_execution_cycle`` invokes ``run_execution_chain`` for tier-qualified markets."""
    from trading_ai.shark import scan_execute as se
    from trading_ai.shark.execution import ChainResult
    from trading_ai.shark.models import (
        HuntSignal,
        HuntType,
        MarketSnapshot,
        OpportunityTier,
        ScoredOpportunity,
    )
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0))

    m = MarketSnapshot(
        market_id="wire-test-1",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.51,
        volume_24h=5000.0,
        time_to_resolution_seconds=8000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="cat1",
    )
    hs = [
        HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.05, 0.8, {}),
        HuntSignal(HuntType.DEAD_MARKET_CONVERGENCE, 0.08, 0.75, {"side": "yes"}),
    ]
    scored = ScoredOpportunity(
        market=m,
        hunts=hs,
        edge_size=0.08,
        confidence=0.7,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        score=0.85,
        tier=OpportunityTier.TIER_A,
        tier_sizing_multiplier=1.3,
    )

    monkeypatch.setattr(se, "scan_markets", lambda fetchers, fallback_demo=False: [m])
    monkeypatch.setattr(se, "run_hunts_on_market", lambda *a, **k: hs)
    monkeypatch.setattr(se, "score_opportunity", lambda mm, hh: scored)

    calls = []

    def capture(s, **kwargs):
        calls.append((s.market.market_id, kwargs.get("outlet")))
        return ChainResult(True, "complete", [], s, 0.0)

    monkeypatch.setattr(se, "run_execution_chain", capture)

    class FF:
        outlet_name = "kalshi"

        def fetch_binary_markets(self):
            return [m]

    n, att = se.run_scan_execution_cycle((FF(),), tag="unit")
    assert n == 1
    assert att == 1
    assert len(calls) == 1
    assert calls[0][0] == "wire-test-1"
    assert calls[0][1] == "kalshi"


def test_91b_scan_skips_live_execution_for_polymarket(tmp_path, monkeypatch):
    """Polymarket markets are scanned but do not invoke ``run_execution_chain`` (Kalshi-only live)."""
    from trading_ai.shark import scan_execute as se
    from trading_ai.shark.models import (
        HuntSignal,
        HuntType,
        MarketSnapshot,
        OpportunityTier,
        ScoredOpportunity,
    )
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0))

    m = MarketSnapshot(
        market_id="poly:abc",
        outlet="polymarket",
        yes_price=0.45,
        no_price=0.55,
        volume_24h=5000.0,
        time_to_resolution_seconds=8000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="cat1",
    )
    hs = [HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.12, 0.8, {})]
    scored = ScoredOpportunity(
        market=m,
        hunts=hs,
        edge_size=0.12,
        confidence=0.7,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        score=0.85,
        tier=OpportunityTier.TIER_A,
        tier_sizing_multiplier=1.3,
    )

    monkeypatch.setattr(se, "scan_markets", lambda fetchers, fallback_demo=False: [m])
    monkeypatch.setattr(se, "run_hunts_on_market", lambda *a, **k: hs)
    monkeypatch.setattr(se, "score_opportunity", lambda mm, hh: scored)

    def boom(*a, **k):
        raise AssertionError("run_execution_chain must not run for polymarket")

    monkeypatch.setattr(se, "run_execution_chain", boom)

    class FF:
        outlet_name = "polymarket"

        def fetch_binary_markets(self):
            return [m]

    n, att = se.run_scan_execution_cycle((FF(),), tag="unit_poly")
    assert n == 1
    assert att == 1


def test_polymarket_submit_order_blocked_by_default(monkeypatch):
    from trading_ai.shark.execution_live import submit_order
    from trading_ai.shark.models import ExecutionIntent, HuntType

    monkeypatch.delenv("POLY_EXECUTION_ENABLED", raising=False)
    intent = ExecutionIntent(
        market_id="0x1",
        outlet="polymarket",
        side="yes",
        stake_fraction_of_capital=0.01,
        edge_after_fees=0.1,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.PURE_ARBITRAGE],
        source="test",
        expected_price=0.5,
        notional_usd=1.0,
        shares=1,
    )
    r = submit_order(intent)
    assert r.success is False
    assert r.status == "disabled"
    assert "disabled" in (r.reason or "").lower()


def test_polymarket_submit_order_geoblock_when_env_true(monkeypatch):
    from trading_ai.shark.execution_live import submit_order
    from trading_ai.shark.models import ExecutionIntent, HuntType

    monkeypatch.setenv("POLY_EXECUTION_ENABLED", "true")
    intent = ExecutionIntent(
        market_id="0x1",
        outlet="polymarket",
        side="yes",
        stake_fraction_of_capital=0.01,
        edge_after_fees=0.1,
        estimated_win_probability=0.55,
        hunt_types=[HuntType.PURE_ARBITRAGE],
        source="test",
        expected_price=0.5,
        notional_usd=1.0,
        shares=1,
    )
    r = submit_order(intent)
    assert r.success is False
    assert r.status == "geo_blocked"
    assert "geoblock" in (r.reason or "").lower() or "scan" in (r.reason or "").lower()


def test_hunt_kalshi_near_close_fires_within_24h_at_70pct_yes():
    import time

    from trading_ai.shark.kalshi_hunts import hunt_kalshi_near_close
    from trading_ai.shark.models import MarketSnapshot

    end = time.time() + 12 * 3600
    m = MarketSnapshot(
        market_id="KX-NC",
        outlet="kalshi",
        yes_price=0.72,
        no_price=0.28,
        volume_24h=5000.0,
        time_to_resolution_seconds=12 * 3600.0,
        resolution_criteria="Fed",
        last_price_update_timestamp=time.time(),
        end_date_seconds=end,
    )
    sig = hunt_kalshi_near_close(m)
    assert sig is not None
    assert sig.hunt_type.value == "kalshi_near_close"
    assert sig.details.get("side") == "yes"


def test_hunt_kalshi_near_close_fires_weak_yes_bet_no():
    import time

    from trading_ai.shark.kalshi_hunts import hunt_kalshi_near_close
    from trading_ai.shark.models import MarketSnapshot

    end = time.time() + 6 * 3600
    m = MarketSnapshot(
        market_id="KX-NC2",
        outlet="kalshi",
        yes_price=0.25,
        no_price=0.75,
        volume_24h=5000.0,
        time_to_resolution_seconds=6 * 3600.0,
        resolution_criteria="Test",
        last_price_update_timestamp=time.time(),
        end_date_seconds=end,
    )
    sig = hunt_kalshi_near_close(m)
    assert sig is not None
    assert sig.details.get("side") == "no"


def test_map_kalshi_prefers_explicit_yes_price_field():
    from trading_ai.shark.outlets.kalshi import map_kalshi_market_to_snapshot

    now = 1_700_000_000.0
    m = {
        "ticker": "KX-P",
        "yes_ask": 50,
        "no_ask": 50,
        "yes_price": 62,
        "no_price": 38,
        "volume_24h": 100.0,
        "title": "Alt fields",
        "close_time": now + 3600.0,
    }
    sn = map_kalshi_market_to_snapshot(m, now)
    # yes_bid absent; yes_price is tried before yes_ask in field order
    assert abs(sn.yes_price - 0.62) < 1e-9
    assert abs(sn.no_price - 0.38) < 1e-9


def test_hunt_kalshi_momentum_fires_on_five_percent_move():
    import time

    from trading_ai.shark.kalshi_hunts import hunt_kalshi_momentum
    from trading_ai.shark.models import MarketSnapshot

    ph = {"KX-M": [0.40, 0.42, 0.51]}
    m = MarketSnapshot(
        market_id="KX-M",
        outlet="kalshi",
        yes_price=0.51,
        no_price=0.49,
        volume_24h=5000.0,
        time_to_resolution_seconds=86400.0,
        resolution_criteria="x",
        last_price_update_timestamp=time.time(),
    )
    sig = hunt_kalshi_momentum(m, price_history=ph)
    assert sig is not None
    assert sig.hunt_type.value == "kalshi_momentum"


def test_kalshi_ssl_context_uses_certifi_when_available():
    import ssl

    from trading_ai.shark.outlets.kalshi import _get_ssl_context

    ctx = _get_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)


def test_scheduler_kalshi_hf_runs_every_30_seconds():
    from datetime import timedelta

    from trading_ai.shark.scheduler import build_shark_scheduler

    sched = build_shark_scheduler(
        standard_scan=lambda: None,
        hot_scan=lambda: None,
        gap_passive_scan=lambda: None,
        gap_active_scan=lambda: None,
        resolution_monitor=lambda: None,
        daily_memo=lambda: None,
        weekly_summary=lambda: None,
        state_backup=lambda: None,
        health_check=lambda: None,
        hot_window_active=lambda: False,
        gap_active=lambda: False,
        kalshi_hf_scan=lambda: None,
    )
    assert sched is not None
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "kalshi_hf" in jobs
    assert jobs["kalshi_hf"].trigger.interval == timedelta(seconds=30)


def test_scheduler_kalshi_full_and_ceo_jobs_registered():
    from trading_ai.shark.scheduler import build_shark_scheduler

    sched = build_shark_scheduler(
        standard_scan=lambda: None,
        hot_scan=lambda: None,
        gap_passive_scan=lambda: None,
        gap_active_scan=lambda: None,
        resolution_monitor=lambda: None,
        daily_memo=lambda: None,
        weekly_summary=lambda: None,
        state_backup=lambda: None,
        health_check=lambda: None,
        hot_window_active=lambda: False,
        gap_active=lambda: False,
        ceo_session=lambda _s: None,
        kalshi_full_scan=lambda: None,
        kalshi_convergence_scan=lambda: None,
        kalshi_hf_scan=lambda: None,
    )
    ids = [j.id for j in sched.get_jobs()]
    assert "kalshi_full" in ids
    assert "kalshi_convergence" in ids
    assert sum(1 for i in ids if str(i).startswith("ceo_")) == 4


def test_execution_submit_fail_does_not_use_telegram():
    import inspect

    from trading_ai.shark import execution

    src = inspect.getsource(execution.run_execution_chain)
    fail_ret = src.index('return ChainResult(False, "submit_failed"')
    window = src[max(0, fail_ret - 500) : fail_ret + 80]
    assert "submit_order" in window
    assert "send_telegram" not in window


def test_74_kalshi_401_does_not_block_all_systems_go(tmp_path, monkeypatch):
    """Kalshi auth failure is non-fatal for setup_env — compound elsewhere."""
    se = _load_setup_env()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("STARTING_CAPITAL", "50")
    monkeypatch.setenv("KALSHI_API_KEY", "bad-key")
    monkeypatch.setenv("MANIFOLD_API_KEY", "test-manifold-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("FRED_API_KEY", "test-fred")
    monkeypatch.delenv("POLY_WALLET_KEY", raising=False)
    monkeypatch.setenv("POLY_API_KEY", "")
    with patch.object(se, "test_polymarket", return_value=(True, "ok")), patch.object(
        se, "test_kalshi", return_value=(True, "scan_only_401")
    ), patch.object(se, "test_manifold", return_value=(True, "ok")), patch.object(se, "test_telegram", return_value=(True, "ok")), patch(
        "trading_ai.shark.outlets.polymarket.test_polymarket_credentials",
        return_value={
            "status_code": 200,
            "error": None,
            "balance": None,
            "key_id_used": "",
            "secret_set": False,
            "wallet_set": False,
        },
    ):
        assert se.main() == 0


def test_65_wallet_scanner_identifies_category_specialist():
    from trading_ai.shark.wallet_intel import WalletProfileView, pattern_category_specialist

    w = WalletProfileView(
        wallet_address="0xabc1",
        total_trades=120,
        win_rate_overall=0.52,
        win_rate_by_category={"crypto": 0.78},
        trades_by_category={"crypto": 24},
    )
    out = pattern_category_specialist(w)
    assert out is not None
    assert out[0] == "crypto"
    assert out[1] > 0.75


def test_66_wallet_scanner_identifies_near_zero_accumulator_pattern():
    from trading_ai.shark.wallet_intel import WalletProfileView, pattern_near_zero_accumulator

    w = WalletProfileView(
        wallet_address="0xdef2",
        total_trades=12,
        average_entry_price=0.08,
        average_exit_price=0.85,
    )
    assert pattern_near_zero_accumulator(w) is True


def test_67_copy_trade_runs_through_hunt_engine_before_executing():
    from trading_ai.shark import wallet_intel as wi

    calls = []

    def fake_run(m, **kwargs):
        calls.append("hunt")
        return [HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})]

    m = MarketSnapshot(
        market_id="m67",
        outlet="polymarket",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        market_category="crypto",
    )
    with patch.object(wi, "run_hunts_on_market", side_effect=fake_run):
        wi.evaluate_copy_trade_tier(m, {"tracked_wallets": []})
    assert calls == ["hunt"]


def test_68_hunt6_detects_near_zero_opportunity():
    m = MarketSnapshot(
        market_id="h6",
        outlet="polymarket",
        yes_price=0.08,
        no_price=0.92,
        volume_24h=1500.0,
        time_to_resolution_seconds=10 * 24 * 3600.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        historical_yes_rate=0.35,
        historical_sample_count=40,
        market_category="economics",
    )
    hs = run_hunts_on_market(m)
    assert any(h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION for h in hs)


def test_69_hunt6_sizing_uses_quarter_kelly_base():
    from trading_ai.shark.executor import HUNT6_KELLY_BASE, build_execution_intent
    from unittest.mock import patch

    m = MarketSnapshot(
        market_id="h6k",
        outlet="polymarket",
        yes_price=0.08,
        no_price=0.92,
        volume_24h=1500.0,
        time_to_resolution_seconds=10 * 24 * 3600.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        historical_yes_rate=0.35,
        historical_sample_count=40,
    )
    hs = [h for h in run_hunts_on_market(m) if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION]
    assert len(hs) == 1
    sc = score_opportunity(m, hs + [HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.05, 0.5, {})], strategy_key="t")
    with patch("trading_ai.shark.executor.apply_kelly_scaling") as ak:
        ak.side_effect = lambda fk, kb: fk * kb
        build_execution_intent(sc, capital=500.0, outlet="polymarket", min_edge_effective=0.01)
        kbs = [c[0][1] for c in ak.call_args_list]
    assert HUNT6_KELLY_BASE in kbs
    assert max(kbs) <= 0.25 + 1e-9


def test_70_hunt6_aggregate_exposure_never_exceeds_8pct():
    from trading_ai.shark.executor import build_execution_intent

    m = MarketSnapshot(
        market_id="h6c",
        outlet="polymarket",
        yes_price=0.08,
        no_price=0.92,
        volume_24h=1500.0,
        time_to_resolution_seconds=10 * 24 * 3600.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
        historical_yes_rate=0.35,
        historical_sample_count=40,
    )
    hs = [h for h in run_hunts_on_market(m) if h.hunt_type == HuntType.NEAR_ZERO_ACCUMULATION]
    sc = score_opportunity(m, hs + [HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.05, 0.5, {})], strategy_key="t")
    intent = build_execution_intent(
        sc,
        capital=1000.0,
        outlet="polymarket",
        min_edge_effective=0.01,
        hunt6_aggregate_exposure_usd=75.0,
    )
    assert intent is not None
    assert intent.stake_fraction_of_capital <= 0.005 + 1e-6


def test_71_data_feed_unavailable_does_not_block_execution():
    from trading_ai.shark import data_feeds

    with patch("trading_ai.shark.data_feeds.fetch_fred_macro_snapshot", side_effect=RuntimeError("down")):
        out = data_feeds.load_combined_data_feeds()
    assert out["fred"] is None


def test_72_wallet_registry_persists_and_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.state_store import load_wallets_registry, save_wallets_registry

    payload = {"tracked_wallets": [{"address": "0x1234", "score": 0.9}], "last_full_scan": 1.0}
    save_wallets_registry(payload)
    data = load_wallets_registry()
    assert data["tracked_wallets"][0]["address"] == "0x1234"


def test_97_manifold_routes_to_mana_sandbox_not_execution_chain(tmp_path, monkeypatch):
    from trading_ai.shark import scan_execute as se
    from trading_ai.shark.execution import ChainResult
    from trading_ai.shark.models import (
        HuntSignal,
        HuntType,
        MarketSnapshot,
        OpportunityTier,
        ScoredOpportunity,
    )
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0))

    m = MarketSnapshot(
        market_id="manifold:test-1",
        outlet="manifold",
        yes_price=0.45,
        no_price=0.51,
        volume_24h=5000.0,
        time_to_resolution_seconds=8000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = [
        HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.05, 0.8, {}),
        HuntSignal(HuntType.DEAD_MARKET_CONVERGENCE, 0.08, 0.75, {"side": "yes"}),
    ]
    scored = ScoredOpportunity(
        market=m,
        hunts=hs,
        edge_size=0.08,
        confidence=0.7,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        score=0.85,
        tier=OpportunityTier.TIER_A,
        tier_sizing_multiplier=1.3,
    )

    monkeypatch.setattr(se, "scan_markets", lambda fetchers, fallback_demo=False: [m])
    monkeypatch.setattr(se, "run_hunts_on_market", lambda *a, **k: hs)
    monkeypatch.setattr(se, "score_opportunity", lambda mm, hh: scored)

    chain_calls = []
    mana_calls = []

    def capture_chain(*a, **k):
        chain_calls.append(1)
        return ChainResult(True, "complete", [], a[0] if a else None, 0.0)

    def capture_mana(intent, scored=None):
        mana_calls.append(getattr(intent, "is_mana", None))

    monkeypatch.setattr(se, "run_execution_chain", capture_chain)
    monkeypatch.setattr("trading_ai.shark.mana_sandbox.execute_mana_trade", capture_mana)

    class FF:
        outlet_name = "manifold"

        def fetch_binary_markets(self):
            return [m]

    n, att = se.run_scan_execution_cycle((FF(),), tag="unit")
    assert n == 1
    assert att == 1
    assert chain_calls == []
    assert mana_calls == [True]


def test_98_mana_trade_does_not_update_capital_json(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.mana_sandbox import update_mana_outcome
    from trading_ai.shark.models import HuntType

    save_capital(CapitalRecord(current_capital=25.0, peak_capital=25.0, starting_capital=25.0))
    before = load_capital().current_capital
    update_mana_outcome(
        "m1",
        "YES",
        10.0,
        strategy="shark_default",
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        win=True,
    )
    assert load_capital().current_capital == before


def test_99_mana_outcome_updates_bayesian_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.mana_sandbox import update_mana_outcome
    from trading_ai.shark.models import HuntType

    w0 = BAYES.outlet_weights.get("manifold", 0.5)
    update_mana_outcome(
        "m2",
        "YES",
        5.0,
        strategy="shark_default",
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        win=True,
    )
    w1 = BAYES.outlet_weights.get("manifold", 0.5)
    assert w1 != w0


def test_100_mana_summary_returns_expected_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.shark.mana_sandbox import get_mana_summary

    s = get_mana_summary()
    for key in (
        "mana_balance",
        "mana_starting",
        "mana_peak",
        "total_mana_trades",
        "winning_mana_trades",
        "mana_win_rate",
        "strategy_performance",
        "last_updated",
        "monthly_target_mana",
        "growth_multiplier",
        "open_mana_positions",
    ):
        assert key in s


def test_100a_mana_loss_postmortem_and_learning_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.governance.storage_architecture import shark_state_path
    from trading_ai.shark import mana_sandbox as ms

    st = ms.load_mana_state()
    st["mana_resolution_history"] = [
        {
            "resolved_at": 1000.0,
            "market_id": "manifold:x",
            "outcome": "loss",
            "hunt_type_used": "near_resolution",
            "hunt_types": ["near_resolution"],
            "edge_detected": 0.04,
            "side_taken": "YES",
            "mana_staked": 10.0,
            "mana_lost": 10.0,
            "mana_pnl": -10.0,
            "claude_reasoning": "test",
            "actual_resolution": "NO",
        }
    ]
    st["last_claude_loss_analysis_max_resolved_at"] = 0.0
    ms.save_mana_state(st)
    pm = ms.get_loss_postmortem()
    assert pm["total_losses"] == 1
    assert pm["total_mana_lost"] == 10.0
    assert "near_resolution" in pm["losing_hunt_types"]

    monkeypatch.setattr(
        "trading_ai.shark.claude_eval.claude_analyze_losses",
        lambda _p: {
            "root_cause": "test",
            "patterns": ["p1"],
            "parameter_changes": {"hunt_type_to_disable": [], "min_edge_adjustment": {}},
            "recovery_strategy": "wait",
            "confidence_in_analysis": 0.5,
        },
        raising=False,
    )
    monkeypatch.setattr(
        "trading_ai.shark.reporting.send_loss_postmortem_alert",
        lambda *_a, **_k: True,
        raising=False,
    )
    rep = ms.maybe_run_mana_loss_learning_on_startup()
    assert rep.get("ran") is True
    rep2 = ms.maybe_run_mana_loss_learning_on_startup()
    assert rep2.get("ran") is False
    lf = shark_state_path("claude_learnings.json")
    assert lf.is_file()


def test_101_kalshi_still_routes_to_run_execution_chain(tmp_path, monkeypatch):
    from trading_ai.shark import scan_execute as se
    from trading_ai.shark.execution import ChainResult
    from trading_ai.shark.models import (
        HuntSignal,
        HuntType,
        MarketSnapshot,
        OpportunityTier,
        ScoredOpportunity,
    )
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0))

    m = MarketSnapshot(
        market_id="kx-real-1",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.51,
        volume_24h=5000.0,
        time_to_resolution_seconds=8000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = [
        HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.05, 0.8, {}),
        HuntSignal(HuntType.DEAD_MARKET_CONVERGENCE, 0.08, 0.75, {"side": "yes"}),
    ]
    scored = ScoredOpportunity(
        market=m,
        hunts=hs,
        edge_size=0.08,
        confidence=0.7,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        score=0.85,
        tier=OpportunityTier.TIER_A,
        tier_sizing_multiplier=1.3,
    )

    monkeypatch.setattr(se, "scan_markets", lambda fetchers, fallback_demo=False: [m])
    monkeypatch.setattr(se, "run_hunts_on_market", lambda *a, **k: hs)
    monkeypatch.setattr(se, "score_opportunity", lambda mm, hh: scored)

    calls = []

    def capture(s, **kwargs):
        calls.append((kwargs.get("outlet"), kwargs.get("capital")))
        return ChainResult(True, "complete", [], s, 0.0)

    monkeypatch.setattr(se, "run_execution_chain", capture)

    class FF:
        outlet_name = "kalshi"

        def fetch_binary_markets(self):
            return [m]

    n, att = se.run_scan_execution_cycle((FF(),), tag="unit")
    assert n == 1
    assert att == 1
    assert len(calls) == 1
    assert calls[0][0] == "kalshi"


def test_111_phase_1_margin_capped_at_20pct():
    from trading_ai.shark.margin_control import get_margin_allowance

    a = get_margin_allowance(25.0, 0.9, "TIER_A", 0.05, near_zero_hunt=False)
    assert a == pytest.approx(5.0)


def test_112_phase_3_margin_capped_at_10pct_when_confidence_high():
    from trading_ai.shark.margin_control import get_margin_allowance

    a = get_margin_allowance(5000.0, 0.85, "TIER_A", 0.05, near_zero_hunt=False)
    assert a == pytest.approx(500.0)


def test_113_phase_3_margin_capped_at_7pct_when_confidence_low():
    from trading_ai.shark.margin_control import get_margin_allowance

    a = get_margin_allowance(5000.0, 0.75, "TIER_B", 0.05, near_zero_hunt=False)
    assert a == pytest.approx(350.0)


def test_114_margin_blocked_when_drawdown_over_15pct():
    from trading_ai.shark.margin_control import get_margin_allowance

    assert get_margin_allowance(5000.0, 0.9, "TIER_A", 0.16, near_zero_hunt=False) == 0.0


def test_115_margin_blocked_for_tier_c_always():
    from trading_ai.shark.margin_control import get_margin_allowance

    assert get_margin_allowance(5000.0, 0.99, "TIER_C", 0.0, near_zero_hunt=False) == 0.0


def test_116_margin_blocked_for_hunt6_near_zero():
    from trading_ai.shark.margin_control import get_margin_allowance

    assert get_margin_allowance(5000.0, 0.99, "TIER_A", 0.0, near_zero_hunt=True) == 0.0


def test_117_hard_check_in_execution_chain_blocks_oversized_margin_trade(tmp_path, monkeypatch):
    from trading_ai.shark.execution import run_execution_chain
    from trading_ai.shark.models import (
        ExecutionIntent,
        HuntSignal,
        HuntType,
        MarketSnapshot,
        OpportunityTier,
        ScoredOpportunity,
    )

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    m = MarketSnapshot(
        market_id="margin-block-1",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.55,
        volume_24h=1000.0,
        time_to_resolution_seconds=8000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = [HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.08, 0.85, {})]
    scored = ScoredOpportunity(
        market=m,
        hunts=hs,
        edge_size=0.08,
        confidence=0.85,
        liquidity_score=0.9,
        resolution_speed_score=0.8,
        strategy_performance_weight=0.5,
        score=0.85,
        tier=OpportunityTier.TIER_A,
        tier_sizing_multiplier=1.3,
    )
    bad = ExecutionIntent(
        market_id="margin-block-1",
        outlet="kalshi",
        side="yes",
        stake_fraction_of_capital=0.15,
        edge_after_fees=0.08,
        estimated_win_probability=0.6,
        hunt_types=[HuntType.STRUCTURAL_ARBITRAGE],
        source="shark_compounding",
        notional_usd=500.0,
        expected_price=0.45,
        shares=100,
        meta={"margin_borrowed": 400.0, "tier": "TIER_A", "margin_cap_pct": 0.2},
    )
    monkeypatch.setattr("trading_ai.shark.execution.build_execution_intent", lambda *a, **k: bad)

    r = run_execution_chain(scored, capital=50.0, outlet="kalshi", peak_capital=50.0, execute_live=False)
    assert r.ok is False
    assert r.halted_at == "margin_unsafe"


def test_118_margin_status_returns_correct_structure(tmp_path, monkeypatch):
    from trading_ai.shark.margin_control import get_margin_status
    from trading_ai.shark.state_store import CapitalRecord, save_capital

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    save_capital(CapitalRecord(current_capital=100.0, peak_capital=100.0, starting_capital=100.0))

    s = get_margin_status()
    for k in (
        "deposited_capital",
        "max_borrowable",
        "currently_borrowed",
        "remaining_margin",
        "margin_pct",
        "margin_allowed",
    ):
        assert k in s
    assert isinstance(s["deposited_capital"], (int, float))
    assert isinstance(s["margin_allowed"], bool)


def test_119_polymarket_balance_fetch_returns_float_when_credentials_set(monkeypatch):
    import base64
    import json

    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pk = Ed25519PrivateKey.generate()
    raw = pk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("POLY_API_KEY", "test-access-key")
    monkeypatch.setenv("POLY_API_SECRET", base64.b64encode(raw).decode("ascii"))

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"balance": 25.0}).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: Resp())

    from trading_ai.shark.outlets.polymarket import fetch_polymarket_balance

    bal = fetch_polymarket_balance()
    assert isinstance(bal, float)
    assert bal == pytest.approx(25.0)


def test_120_polymarket_api_signing_produces_valid_ed25519_signature():
    import base64

    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from trading_ai.shark.outlets.polymarket import sign_polymarket_request

    pk = Ed25519PrivateKey.generate()
    raw = pk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    secret_b64 = base64.b64encode(raw).decode("ascii")
    ts = 1_700_000_000_000
    sig_b64 = sign_polymarket_request(ts, secret_b64)
    sig = base64.b64decode(sig_b64)
    pk.public_key().verify(sig, str(ts).encode("utf-8"))

    secret_unpadded = secret_b64.rstrip("=")
    sig_b64_u = sign_polymarket_request(ts, secret_unpadded)
    sig_u = base64.b64decode(sig_b64_u)
    pk.public_key().verify(sig_u, str(ts).encode("utf-8"))


def test_121_is_short_resolution_market_true_when_end_date_within_60_min():
    import time

    from trading_ai.shark.crypto_polymarket_hunts import _is_short_resolution_market

    end = time.time() + 45 * 60
    m = MarketSnapshot(
        market_id="poly:sr",
        outlet="polymarket",
        yes_price=0.5,
        no_price=0.5,
        volume_24h=1000.0,
        time_to_resolution_seconds=99999.0,
        resolution_criteria="",
        last_price_update_timestamp=time.time(),
        end_date_seconds=end,
    )
    assert _is_short_resolution_market(m) is True


def test_122_calc_crypto_prob_returns_probability_in_zero_one():
    pytest.importorskip("scipy")
    from trading_ai.shark.crypto_polymarket_hunts import calc_crypto_prob

    p = calc_crypto_prob(100_000.0, 99_000.0, 5.0)
    assert 0.0 < p < 1.0


def test_123_hunt_pure_arbitrage_detects_yes_048_no_049():
    from trading_ai.shark.crypto_polymarket_hunts import hunt_pure_arbitrage

    m = MarketSnapshot(
        market_id="poly:a",
        outlet="polymarket",
        yes_price=0.48,
        no_price=0.49,
        volume_24h=2000.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
    )
    r = hunt_pure_arbitrage(m)
    assert r is not None
    assert r.edge_after_fees == pytest.approx(0.03, abs=1e-6)


def test_124_hunt_near_resolution_fires_when_yes_098_and_10min_left():
    import time

    from trading_ai.shark.crypto_polymarket_hunts import hunt_near_resolution

    end = time.time() + 10 * 60
    m = MarketSnapshot(
        market_id="poly:n",
        outlet="polymarket",
        yes_price=0.98,
        no_price=0.02,
        volume_24h=2000.0,
        time_to_resolution_seconds=600.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
        end_timestamp_unix=end,
        end_date_seconds=end,
    )
    r = hunt_near_resolution(m)
    assert r is not None
    assert r.hunt_type == HuntType.NEAR_RESOLUTION


def test_125_hunt_order_book_imbalance_fires_when_yes_side_thin():
    from trading_ai.shark.crypto_polymarket_hunts import hunt_order_book_imbalance

    m = MarketSnapshot(
        market_id="poly:o",
        outlet="polymarket",
        yes_price=0.5,
        no_price=0.5,
        volume_24h=2000.0,
        time_to_resolution_seconds=86400.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
        best_ask_yes=10.0,
        best_ask_no=90.0,
    )
    r = hunt_order_book_imbalance(m)
    assert r is not None
    assert r.details.get("side") == "yes"


def test_125b_hunt_volume_spike_fires_on_high_volume_contested():
    from trading_ai.shark.crypto_polymarket_hunts import hunt_volume_spike

    m = MarketSnapshot(
        market_id="poly:v",
        outlet="kalshi",
        yes_price=0.48,
        no_price=0.52,
        volume_24h=6000.0,
        time_to_resolution_seconds=86400.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
    )
    r = hunt_volume_spike(m)
    assert r is not None
    assert r.hunt_type == HuntType.VOLUME_SPIKE
    assert r.details.get("side") in ("yes", "no")


def test_gamma_fetch_skips_cleanly_on_enomem(monkeypatch):
    from trading_ai.shark.outlets import polymarket as poly

    def boom(*_a, **_k):
        raise OSError(12, "Cannot allocate memory")

    monkeypatch.setattr(poly.requests, "get", boom)
    assert poly.fetch_gamma_markets_page(limit=10, offset=0) == []


def test_126_crypto_scalp_scan_interval_job_registered():
    from trading_ai.shark.scheduler import build_shark_scheduler

    sched = build_shark_scheduler(
        standard_scan=lambda: None,
        hot_scan=lambda: None,
        gap_passive_scan=lambda: None,
        gap_active_scan=lambda: None,
        resolution_monitor=lambda: None,
        daily_memo=lambda: None,
        weekly_summary=lambda: None,
        state_backup=lambda: None,
        health_check=lambda: None,
        hot_window_active=lambda: False,
        gap_active=lambda: False,
        crypto_scalp_scan=lambda: None,
        near_resolution_sweep=lambda: None,
        arb_sweep=lambda: None,
        kalshi_near_resolution=lambda: None,
    )
    assert sched is not None
    ids = [j.id for j in sched.get_jobs()]
    assert "crypto_scalp_scan" in ids
    assert "near_resolution_sweep" in ids
    assert "arb_sweep" in ids
    assert "kalshi_near_resolution" in ids


def test_127_polymarket_uses_limit_slippage_not_market_order_api():
    from pathlib import Path

    import trading_ai.shark.polymarket_live as pl

    assert pl.limit_price_with_slippage(0.50) == pytest.approx(0.501, abs=1e-9)
    src = Path(pl.__file__).read_text(encoding="utf-8")
    assert "create_market_order" not in src
    assert "create_and_post_order" in src


def test_128_claude_evaluate_trade_returns_valid_json(monkeypatch):
    import sys
    import types

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    fake_mod = types.ModuleType("anthropic")

    class _Anthropic:
        def __init__(self, api_key=None):
            pass

        class _Messages:
            @staticmethod
            def create(**kwargs):
                class _Blk:
                    text = (
                        '{"decision": "YES", "true_probability": 0.62, '
                        '"confidence": 0.71, "size_multiplier": 1.1, "reasoning": "Edge looks real."}'
                    )

                class _Resp:
                    content = [_Blk()]

                return _Resp()

        messages = _Messages()

    fake_mod.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

    from trading_ai.shark.claude_eval import claude_evaluate_trade

    out = claude_evaluate_trade(
        "Will X happen?",
        "kalshi",
        0.45,
        0.55,
        "statistical_window",
        0.08,
        "YES",
    )
    assert out is not None
    assert out["decision"] == "YES"
    assert 0.0 <= out["true_probability"] <= 1.0
    assert "reasoning" in out


def test_129_claude_skip_blocks_execution(monkeypatch):
    m = MarketSnapshot(
        market_id="claude-skip",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = run_hunts_on_market(m)
    from trading_ai.shark.models import HuntSignal

    sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})], strategy_key="t")
    monkeypatch.setattr(
        "trading_ai.shark.claude_eval.apply_claude_evaluator_gate",
        lambda scored, intent, *, capital: (False, "claude_skip"),
    )
    res = run_execution_chain(sc, capital=80.0, outlet="kalshi", execute_live=False)
    assert not res.ok
    assert res.halted_at == "claude_skip"


def test_130_claude_override_changes_side(monkeypatch):
    m = MarketSnapshot(
        market_id="claude-ov",
        outlet="kalshi",
        yes_price=0.45,
        no_price=0.50,
        volume_24h=2000.0,
        time_to_resolution_seconds=4000.0,
        resolution_criteria="test",
        last_price_update_timestamp=0.0,
    )
    hs = run_hunts_on_market(m)
    from trading_ai.shark.models import HuntSignal

    sc = score_opportunity(m, [hs[0], HuntSignal(HuntType.STATISTICAL_WINDOW, 0.09, 0.7, {})], strategy_key="t")

    def _apply(scored, intent, *, capital):
        intent.side = "no"
        intent.meta["claude_reasoning"] = "Prefer NO"
        intent.meta["claude_confidence"] = 0.8
        intent.meta["claude_decision"] = "NO"
        intent.meta["claude_true_probability"] = 0.4
        return True, ""

    monkeypatch.setattr("trading_ai.shark.claude_eval.apply_claude_evaluator_gate", _apply)
    res = run_execution_chain(sc, capital=80.0, outlet="kalshi", execute_live=False)
    assert res.ok
    assert res.intent is not None
    assert res.intent.side == "no"
    assert res.intent.meta.get("claude_reasoning") == "Prefer NO"


def test_131_claude_reasoning_in_telegram_format():
    from trading_ai.shark.reporting import format_trade_fired

    t = format_trade_fired(
        hunt="structural_arbitrage",
        tier="TIER_A",
        outlet="kalshi",
        position_dollars=50.0,
        edge_pct=0.08,
        market_desc="Test market",
        resolves_in="1d",
        claude_reasoning="Model agrees with structural mispricing.",
        claude_confidence=0.82,
    )
    assert "Claude: Model agrees" in t
    assert "Confidence: 82%" in t


def test_132_volume_spike_fires_when_volume_gt_5000_and_yes_near_045():
    from trading_ai.shark.crypto_polymarket_hunts import hunt_volume_spike

    m = MarketSnapshot(
        market_id="poly:vs",
        outlet="polymarket",
        yes_price=0.45,
        no_price=0.55,
        volume_24h=7500.0,
        time_to_resolution_seconds=3600.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
    )
    r = hunt_volume_spike(m)
    assert r is not None
    assert r.hunt_type == HuntType.VOLUME_SPIKE
    assert r.details.get("side") == "yes"


def test_133_hunt_near_resolution_fires_at_093_threshold():
    import time

    from trading_ai.shark.crypto_polymarket_hunts import hunt_near_resolution

    end = time.time() + 15 * 60
    m = MarketSnapshot(
        market_id="poly:n93",
        outlet="polymarket",
        yes_price=0.935,
        no_price=0.065,
        volume_24h=2000.0,
        time_to_resolution_seconds=900.0,
        resolution_criteria="",
        last_price_update_timestamp=0.0,
        end_timestamp_unix=end,
        end_date_seconds=end,
    )
    r = hunt_near_resolution(m)
    assert r is not None
    assert r.hunt_type == HuntType.NEAR_RESOLUTION


def test_134_three_sweep_jobs_near_resolution_arb_kalshi_registered():
    from trading_ai.shark.scheduler import build_shark_scheduler

    sched = build_shark_scheduler(
        standard_scan=lambda: None,
        hot_scan=lambda: None,
        gap_passive_scan=lambda: None,
        gap_active_scan=lambda: None,
        resolution_monitor=lambda: None,
        daily_memo=lambda: None,
        weekly_summary=lambda: None,
        state_backup=lambda: None,
        health_check=lambda: None,
        hot_window_active=lambda: False,
        gap_active=lambda: False,
        near_resolution_sweep=lambda: None,
        arb_sweep=lambda: None,
        kalshi_near_resolution=lambda: None,
    )
    ids = [j.id for j in sched.get_jobs()]
    assert "near_resolution_sweep" in ids
    assert "arb_sweep" in ids
    assert "kalshi_near_resolution" in ids
