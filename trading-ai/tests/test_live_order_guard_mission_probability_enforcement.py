from __future__ import annotations

import os

import pytest


def _set_live_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("NTE_EXECUTION_MODE", "live")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("NTE_PAPER_MODE", "false")
    monkeypatch.setenv("NTE_DRY_RUN", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("NTE_EXECUTION_SCOPE", "live")
    monkeypatch.setenv("NTE_COINBASE_EXECUTION_ROUTE", "live")
    # Keep control artifact preflight off for unit tests (smoke handles end-to-end wiring).
    monkeypatch.setenv("EZRAS_CONTROL_ARTIFACT_PREFLIGHT", "false")
    # Mission tier tests are not governance/joint-review tests — keep advisory mode.
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "false")
    # Gap engine thresholds must be configured for live-order guard evaluation.
    # Set permissive minimums for unit tests; execution still remains fail-closed on missing candidate fields.
    monkeypatch.setenv("GAP_MIN_CONFIDENCE_SCORE", "0.0")
    monkeypatch.setenv("GAP_MIN_EDGE_PERCENT", "-9999")
    monkeypatch.setenv("GAP_MIN_LIQUIDITY_SCORE", "0.0")
    # Use deployment micro-validation isolation keys to avoid duplicate-window collisions in unit tests.
    monkeypatch.setenv("EZRAS_DEPLOYMENT_MICRO_VALIDATION_ACTIVE", "true")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_SESSION_ID", "pytest_session")
    monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "0")


def _valid_candidate_dict() -> dict:
    # Minimal valid UniversalGapCandidate dict for require_valid_candidate_for_execution + evaluate_candidate.
    return {
        "candidate_id": "ugc_test",
        "edge_percent": 10.0,
        "edge_score": 10.0,
        "confidence_score": 0.9,
        "execution_mode": "maker",
        "gap_type": "probability_gap",
        "estimated_true_value": 100.0,
        "liquidity_score": 0.9,
        "fees_estimate": 0.01,
        "slippage_estimate": 0.01,
        "must_trade": True,
        "risk_flags": [],
    }


def test_live_order_guard_enforces_mission_probability_tiers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_live_env(monkeypatch, tmp_path)

    from trading_ai.global_layer.gap_models import authoritative_live_buy_path_set, authoritative_live_buy_path_reset
    from trading_ai.global_layer.gap_models import candidate_context_set, candidate_context_reset
    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted
    from trading_ai.nte.paths import nte_system_health_path
    from trading_ai.nte.utils.atomic_json import atomic_write_json
    from trading_ai.shark.mission import mission_probability_set, mission_probability_reset

    # System health must be green for entry actions.
    atomic_write_json(
        nte_system_health_path(),
        {"healthy": True, "execution_should_pause": False, "global_pause": False, "avenue_pause": {}},
    )

    cand_tok = candidate_context_set(_valid_candidate_dict())  # type: ignore[arg-type]
    auth_tok = authoritative_live_buy_path_set("nte_only")

    try:
        # 0.62 blocked
        monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "1")
        p0 = mission_probability_set(0.62)
        with pytest.raises(RuntimeError, match="mission_probability_tier_blocked"):
            assert_live_order_permitted(
                "place_limit_entry",
                avenue_id="coinbase",
                product_id="BTC-USD",
                order_side="BUY",
                base_size="0.0002",
                quote_notional=10.0,
                credentials_ready=True,
                skip_config_validation=True,
                execution_gate="gate_a",
                quote_balances_for_capital_truth={"USD": 200.0},
                trade_id="t_prob_062",
            )
        mission_probability_reset(p0)

        # 0.70 allowed at small size, blocked when exceeding tier-1 cap ($10 on $200)
        monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "2")
        p1 = mission_probability_set(0.70)
        assert_live_order_permitted(
            "place_limit_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            order_side="BUY",
            base_size="0.0002",
            quote_notional=10.0,
            credentials_ready=True,
            skip_config_validation=True,
            execution_gate="gate_a",
            quote_balances_for_capital_truth={"USD": 200.0},
            trade_id="t_prob_070_ok",
        )
        with pytest.raises(RuntimeError, match="mission_probability_tier_blocked"):
            monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "3")
            assert_live_order_permitted(
                "place_limit_entry",
                avenue_id="coinbase",
                product_id="BTC-USD",
                order_side="BUY",
                base_size="0.0002",
                quote_notional=12.0,
                credentials_ready=True,
                skip_config_validation=True,
                execution_gate="gate_a",
                quote_balances_for_capital_truth={"USD": 200.0},
                trade_id="t_prob_070_block",
            )
        mission_probability_reset(p1)

        # 0.80 less restrictive than 0.70 (tier-2 cap is $20 on $200; $12 allowed)
        monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "4")
        p2 = mission_probability_set(0.80)
        assert_live_order_permitted(
            "place_limit_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            order_side="BUY",
            base_size="0.0002",
            quote_notional=12.0,
            credentials_ready=True,
            skip_config_validation=True,
            execution_gate="gate_a",
            quote_balances_for_capital_truth={"USD": 200.0},
            trade_id="t_prob_080_ok",
        )
        mission_probability_reset(p2)

        # 0.92 strongest allowed tier (still within caps; $30 <= $40 D1 cap, and tier-3 cap=$40)
        monkeypatch.setenv("EZRAS_MICRO_VALIDATION_RUN_INDEX", "5")
        p3 = mission_probability_set(0.92)
        assert_live_order_permitted(
            "place_limit_entry",
            avenue_id="coinbase",
            product_id="BTC-USD",
            order_side="BUY",
            base_size="0.0004",
            quote_notional=30.0,
            credentials_ready=True,
            skip_config_validation=True,
            execution_gate="gate_a",
            quote_balances_for_capital_truth={"USD": 200.0},
            trade_id="t_prob_092_ok",
        )
        mission_probability_reset(p3)
    finally:
        try:
            candidate_context_reset(cand_tok)
        except Exception:
            pass
        try:
            authoritative_live_buy_path_reset(auth_tok)
        except Exception:
            pass

