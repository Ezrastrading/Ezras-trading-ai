"""Run record gates, diagnostics, and outcome classification for micro-validation streak."""

from __future__ import annotations

from trading_ai.deployment import live_micro_validation as lmv


def test_build_run_record_all_green() -> None:
    raw = {
        "trade_id": "live_exec_abc123",
        "coinbase_order_verified": True,
        "order_id_buy": "b1",
        "order_id_sell": "s1",
        "oversell_risk": False,
        "execution_success": True,
        "databank_written": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
        "pipeline": {
            "trade_memory_updated": True,
            "trade_events_appended": True,
            "federated_includes_trade_id": True,
            "supabase_upsert_true": True,
            "supabase_row_exists": True,
            "governance_log_has_entry": True,
            "review_packet_updated": True,
        },
    }
    rec = lmv._build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=True,
        requested_notional_usd=5.0,
        venue_min_notional_usd=5.0,
        chosen_notional_usd=5.0,
    )
    assert rec.get("failed_proof_fields") == []
    assert rec.get("classified_run_outcome") == "passed"
    assert rec.get("run_gate_failure_reason") is None
    assert rec.get("no_partial_failures") is True


def test_build_run_record_blocked_before_execution() -> None:
    raw = {
        "error": "governance_blocked:joint_review_stale",
        "partial_failure_codes": [],
    }
    rec = lmv._build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=True,
        requested_notional_usd=5.0,
        venue_min_notional_usd=5.0,
        chosen_notional_usd=5.0,
    )
    assert rec.get("classified_run_outcome") == "blocked_before_execution"
    assert "buy_fill_confirmed" in (rec.get("failed_proof_fields") or [])


def test_partial_failure_codes_surface_on_run_record() -> None:
    raw = {
        "trade_id": "t1",
        "coinbase_order_verified": True,
        "order_id_buy": "b",
        "order_id_sell": "s",
        "execution_success": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": ["round_trip_incomplete"],
        "pipeline": {},
    }
    rec = lmv._build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=True,
        requested_notional_usd=5.0,
        venue_min_notional_usd=5.0,
        chosen_notional_usd=5.0,
    )
    assert rec.get("no_partial_failures") is False
    assert "no_partial_failures" in (rec.get("failed_proof_fields") or [])
    assert "round_trip_incomplete" in (rec.get("partial_failure_sources") or [])
