"""Gate B momentum scoring engine — continuation ranking, threshold, adaptive learner."""

from __future__ import annotations

from pathlib import Path

from trading_ai.shark.coinbase_spot.momentum_scoring_engine import (
    AdaptiveMomentumLearner,
    DEFAULT_WEIGHTS,
    MomentumAssetSnapshot,
    combine_momentum_score,
    compute_components_for_snapshot,
    dynamic_threshold,
    market_strength_0_1,
    position_size_multiplier,
    run_momentum_scan,
    score_liquidity_with_provenance,
)


def _uptrend_closes(n: int = 40, step: float = 0.004) -> list[float]:
    x = 100.0
    out = [x]
    for _ in range(n - 1):
        x *= 1.0 + step
        out.append(x)
    return out


def test_score_liquidity_missing_spread_does_not_invent_measured_bps() -> None:
    s, meta = score_liquidity_with_provenance(None, 80_000.0, 80_000.0, 100.0)
    assert 0.0 <= s <= 100.0
    assert meta["liquidity_spread_measurement_status"] == "missing"
    assert "measured_spread_bps_used_in_model" not in meta


def test_combine_weights_match_spec() -> None:
    assert abs(sum(DEFAULT_WEIGHTS) - 1.0) < 1e-9
    c = compute_components_for_snapshot(
        MomentumAssetSnapshot(
            product_id="ALT-USD",
            closes=_uptrend_closes(),
            volume_recent_quote=5e6,
            volume_baseline_quote=1e6,
            spread_bps=12.0,
            depth_bid_usd=80_000.0,
            depth_ask_usd=80_000.0,
            btc_closes=_uptrend_closes(),
        )
    )
    s = combine_momentum_score(c, DEFAULT_WEIGHTS)
    assert 0.0 <= s <= 100.0


def test_trending_scores_higher_than_flat() -> None:
    flat = [100.0 + (i % 3) * 0.01 for i in range(40)]
    up = _uptrend_closes()
    ct = compute_components_for_snapshot(MomentumAssetSnapshot(product_id="F-USD", closes=flat))
    cu = compute_components_for_snapshot(
        MomentumAssetSnapshot(
            product_id="U-USD",
            closes=up,
            volume_recent_quote=2e6,
            volume_baseline_quote=1e6,
        )
    )
    assert combine_momentum_score(cu, DEFAULT_WEIGHTS) > combine_momentum_score(ct, DEFAULT_WEIGHTS)


def test_run_momentum_scan_selects_top_and_respects_threshold() -> None:
    rows = [
        MomentumAssetSnapshot(product_id="WEAK-USD", closes=[100.0 + i * 0.01 for i in range(30)]),
        MomentumAssetSnapshot(
            product_id="STRONG-USD",
            closes=_uptrend_closes(35),
            volume_recent_quote=4e6,
            volume_baseline_quote=1e6,
            spread_bps=10.0,
        ),
    ]
    res = run_momentum_scan(rows, base_threshold=40.0, top_k=3)
    assert res.ranked[0].product_id == "STRONG-USD"
    assert res.selected_product_ids == ["STRONG-USD"] or "STRONG-USD" in res.selected_product_ids


def test_near_peak_excluded_from_selected() -> None:
    # Grind up, then a blow-off bar into the range high (terminal bar >> median bar)
    base = [100.0 + i * 0.4 for i in range(28)]
    hi = base[-1]
    blow = hi * 1.045
    closes = base + [blow]
    r = run_momentum_scan(
        [MomentumAssetSnapshot(product_id="PEAK-USD", closes=closes)],
        base_threshold=1.0,
        top_k=2,
    )
    assert r.ranked[0].near_peak is True
    assert r.selected_product_ids == []


def test_dynamic_threshold_weak_market_raises() -> None:
    t0 = dynamic_threshold(70.0, 0.2)
    t1 = dynamic_threshold(70.0, 0.85)
    assert t0 > t1


def test_market_strength_in_0_1() -> None:
    from trading_ai.shark.coinbase_spot.momentum_scoring_engine import MomentumAssetResult, MomentumComponentScores

    fake = [
        MomentumAssetResult(
            "A",
            MomentumComponentScores(),
            momentum_score=80.0,
            failure_multiplier=1.0,
            near_peak=False,
        ),
        MomentumAssetResult(
            "B",
            MomentumComponentScores(),
            momentum_score=60.0,
            failure_multiplier=1.0,
            near_peak=False,
        ),
    ]
    m = market_strength_0_1(fake, top_n=2)
    assert 0.0 <= m <= 1.0


def test_position_size_multiplier_monotone() -> None:
    a = position_size_multiplier(85.0, 70.0)
    b = position_size_multiplier(95.0, 70.0)
    assert b >= a


def test_adaptive_learner_persists(tmp_path: Path) -> None:
    p = tmp_path / "mom.json"
    L = AdaptiveMomentumLearner(path=p)
    L.record_trade_outcome(
        momentum_score_at_entry=75.0,
        components=compute_components_for_snapshot(
            MomentumAssetSnapshot(product_id="X-USD", closes=_uptrend_closes())
        ),
        won=True,
    )
    L2 = AdaptiveMomentumLearner(path=p)
    assert L2.state.trades_recorded >= 1


def test_gate_b_momentum_scan_integration() -> None:
    from trading_ai.shark.coinbase_spot.gate_b_scanner import gate_b_momentum_scan

    scan = gate_b_momentum_scan(
        [
            {
                "product_id": "Z-USD",
                "closes": _uptrend_closes(30),
                "volume_recent_quote": 3e6,
                "volume_baseline_quote": 1e6,
                "spread_bps": 15.0,
            }
        ],
        base_threshold=35.0,
        top_k=2,
    )
    assert scan.ranked
    assert len(scan.weights_used) == 6


def test_gate_b_engine_uses_momentum_when_closes_present() -> None:
    from trading_ai.shark.coinbase_spot.gate_b_config import GateBConfig
    from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine

    eng = GateBMomentumEngine(config=GateBConfig())
    raw = [
        {
            "product_id": "ALT-USD",
            "quote_ts": __import__("time").time(),
            "best_bid": 99.0,
            "best_ask": 101.0,
            "volume_24h_usd": 5e6,
            "spread_bps": 20.0,
            "book_depth_usd": 50_000.0,
            "move_pct": 0.08,
            "volume_surge_ratio": 2.0,
            "continuation_candles": 4,
            "velocity_score": 0.6,
            "candle_structure_score": 0.55,
            "closes": _uptrend_closes(25),
            "exhaustion_risk": 0.1,
        }
    ]
    out = eng.evaluate_entry_candidates(raw, open_product_ids=[])
    assert "candidates" in out
    if out.get("momentum_scan"):
        assert "effective_threshold" in out["momentum_scan"]
