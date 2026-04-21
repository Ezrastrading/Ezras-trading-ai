import os
from pathlib import Path

import pytest


def _set_live_env(tmp_path: Path) -> None:
    os.environ["EZRAS_RUNTIME_ROOT"] = str(tmp_path)
    os.environ["NTE_EXECUTION_MODE"] = "live"
    os.environ["NTE_EXECUTION_SCOPE"] = "live"
    os.environ["NTE_LIVE_TRADING_ENABLED"] = "true"
    os.environ["COINBASE_ENABLED"] = "true"


def _valid_candidate():
    from trading_ai.global_layer.gap_models import UniversalGapCandidate, new_universal_candidate_id

    return UniversalGapCandidate(
        candidate_id=new_universal_candidate_id(prefix="t"),
        edge_percent=10.0,
        edge_score=5.0,
        confidence_score=0.5,
        execution_mode="maker",
        gap_type="price_lag",
        estimated_true_value=101.0,
        liquidity_score=0.9,
        fees_estimate=0.01,
        slippage_estimate=0.01,
        must_trade=True,
    )


def test_live_buy_blocked_without_candidate(tmp_path: Path) -> None:
    _set_live_env(tmp_path)
    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

    with pytest.raises(RuntimeError) as exc:
        assert_live_order_permitted(
            "place_market_entry",
            "coinbase",
            "BTC-USD",
            source="unit_test",
            order_side="BUY",
            quote_notional=10.0,
            credentials_ready=True,
            skip_config_validation=True,
            execution_gate="gate_a",
        )
    assert "missing_or_incomplete_universal_candidate" in str(exc.value)


def test_non_authoritative_live_buy_path_blocked(tmp_path: Path) -> None:
    _set_live_env(tmp_path)
    from trading_ai.global_layer.gap_models import candidate_context_set, candidate_context_reset
    from trading_ai.nte.hardening.live_order_guard import assert_live_order_permitted

    tok = candidate_context_set(_valid_candidate())
    try:
        with pytest.raises(RuntimeError) as exc:
            assert_live_order_permitted(
                "place_market_entry",
                "coinbase",
                "BTC-USD",
                source="unit_test",
                order_side="BUY",
                quote_notional=10.0,
                credentials_ready=True,
                skip_config_validation=True,
                execution_gate="gate_a",
            )
        assert "non_authoritative_live_buy_path_blocked" in str(exc.value)
    finally:
        candidate_context_reset(tok)


def test_post_trade_snapshot_failure_raises_for_live_trade(monkeypatch, tmp_path: Path) -> None:
    _set_live_env(tmp_path)
    from trading_ai.automation import post_trade_hub
    from trading_ai.runtime.trade_snapshots import SnapshotWriteError

    def _boom(*args, **kwargs):
        raise SnapshotWriteError("boom")

    monkeypatch.setattr(post_trade_hub, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr("trading_ai.runtime.trade_snapshots.snapshot_trades_master", _boom, raising=False)

    trade = {"trade_id": "t1", "live_or_paper": "live", "avenue_id": "A", "gate_id": "A_CORE"}
    with pytest.raises(RuntimeError) as exc:
        post_trade_hub.execute_post_trade_placed(None, trade)
    assert "required_snapshot_write_failed" in str(exc.value)


def test_first20_judge_flags_missing_candidate_and_grade(tmp_path: Path) -> None:
    # Build a minimal archive + runtime layout
    archive = tmp_path / "arch"
    archive.mkdir(parents=True)
    runtime = tmp_path / "rt"
    (runtime / "data" / "trades").mkdir(parents=True, exist_ok=True)
    (runtime / "data" / "pnl").mkdir(parents=True, exist_ok=True)
    (runtime / "data" / "risk").mkdir(parents=True, exist_ok=True)

    # Required archive artifacts
    (archive / "session_manifest.json").write_text('{"runtime_root": "' + str(runtime) + '"}', encoding="utf-8")
    (archive / "first_20_session_report.json").write_text(
        '{"trades":[{"trade_id":"t1","status":"closed","federation_included":true,"packet_inclusion_confirmed":true}],"cumulative":{},"recommendation":"PASS_SHADOW_VERIFICATION"}',
        encoding="utf-8",
    )

    # Snapshots: edge has no candidate_id; exec has no execution_grade; review missing
    (runtime / "data" / "trades" / "trades_master.jsonl").write_text(
        '{"trade_id":"t1","universal_gap_candidate":{"must_trade":true}}\n',
        encoding="utf-8",
    )
    (runtime / "data" / "trades" / "trades_edge_snapshot.jsonl").write_text('{"trade_id":"t1"}\n', encoding="utf-8")
    (runtime / "data" / "trades" / "trades_execution_snapshot.jsonl").write_text('{"trade_id":"t1"}\n', encoding="utf-8")
    (runtime / "data" / "trades" / "trades_review_snapshot.jsonl").write_text("", encoding="utf-8")

    # Global artifacts required by judge
    (runtime / "data" / "pnl" / "pnl_record.json").write_text("{}", encoding="utf-8")
    (runtime / "data" / "risk" / "risk_state.json").write_text("{}", encoding="utf-8")

    from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session

    out = judge_first_twenty_session(archive)
    fails = out["universal_candidate_integrity"]["failure_categories_by_trade"]["t1"]
    assert "missing_candidate" in fails
    assert "missing_execution_grade" in fails

