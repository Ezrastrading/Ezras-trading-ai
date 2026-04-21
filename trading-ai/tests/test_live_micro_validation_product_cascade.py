"""Streak blocking_reason and run record must not false-blame Supabase on pre-exec product failure."""

from __future__ import annotations

from trading_ai.deployment.live_micro_validation import (
    _build_run_record,
    _execution_root_analysis,
)


def test_root_analysis_buy_blocked_product_not_allowed() -> None:
    raw = {
        "error": "buy_failed:Live order blocked: product_not_allowed",
        "venue_product_id": "BTC-USDC",
        "trade_id": "live_exec_x",
    }
    r = _execution_root_analysis(raw, checklist={"governance_trading_permitted": True})
    assert r["pre_execution_blocked"] is True
    assert "validation_product_policy_failure" in str(r["streak_blocking_reason"])


def test_root_analysis_quote_precheck_insufficient_quote() -> None:
    raw = {"error": "quote_precheck_failed:insufficient_allowed_quote_balance"}
    r = _execution_root_analysis(raw, checklist={})
    assert r["pre_execution_blocked"] is True
    assert "insufficient_allowed_quote_balance" in str(r["streak_blocking_reason"])


def test_root_analysis_quote_precheck_no_runtime_supported() -> None:
    raw = {"error": "quote_precheck_failed:no_runtime_supported_validation_product"}
    r = _execution_root_analysis(raw, checklist={})
    assert r["pre_execution_blocked"] is True
    assert r["streak_blocking_reason"]


def test_root_analysis_quote_precheck_no_allowed_validation_product_found() -> None:
    raw = {"error": "quote_precheck_failed:no_allowed_validation_product_found"}
    r = _execution_root_analysis(raw, checklist={})
    assert r["pre_execution_blocked"] is True
    assert "no_allowed_validation_product_found" in str(r["streak_blocking_reason"])


def test_root_analysis_quote_precheck_fundable_disallowed() -> None:
    raw = {"error": "quote_precheck_failed:runtime_policy_disallows_fundable_product"}
    r = _execution_root_analysis(raw, checklist={})
    assert r["pre_execution_blocked"] is True
    assert "runtime_policy_disallows_fundable_product" in str(r["streak_blocking_reason"])


def test_build_run_record_pre_exec_supabase_not_failed_state() -> None:
    raw = {
        "error": "quote_precheck_failed:no_runtime_supported_validation_product",
        "venue_product_id": "BTC-USD",
        "supabase_synced": False,
        "governance_logged": False,
        "packet_updated": False,
        "pnl_calculation_verified": False,
        "partial_failure_codes": [],
    }
    rec = _build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=False,
        requested_notional_usd=5.0,
        venue_min_notional_usd=10.0,
        chosen_notional_usd=10.0,
        checklist={"governance_trading_permitted": True},
    )
    assert rec.get("supabase_proof_not_applicable") is True
    assert rec.get("supabase_ok") is True
    assert rec.get("failed_proof_fields") == []


def test_build_run_record_fundable_disallowed_same_na_proofs() -> None:
    raw = {
        "error": "quote_precheck_failed:runtime_policy_disallows_fundable_product",
        "venue_product_id": None,
        "supabase_synced": False,
        "governance_logged": False,
        "packet_updated": False,
        "pnl_calculation_verified": False,
        "partial_failure_codes": [],
    }
    rec = _build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=False,
        requested_notional_usd=5.0,
        venue_min_notional_usd=10.0,
        chosen_notional_usd=10.0,
        checklist={"governance_trading_permitted": True},
    )
    assert rec.get("supabase_proof_not_applicable") is True
    assert rec.get("failed_proof_fields") == []


def test_build_run_record_real_trade_still_strict() -> None:
    raw = {
        "order_id_buy": "oid1",
        "order_id_sell": "oid2",
        "buy_fill_confirmed": True,
        "sell_fill_confirmed": True,
        "base_quote_truth_ok": True,
        "supabase_synced": True,
        "governance_logged": True,
        "packet_updated": True,
        "pnl_calculation_verified": True,
        "partial_failure_codes": [],
        "oversell_risk": False,
        "local_write_evidence_ok": True,
        "databank_written": True,
        "pipeline": {
            "trade_memory_updated": True,
            "trade_events_appended": True,
        },
    }
    rec = _build_run_record(
        1,
        raw,
        recon_ok=True,
        supa_ok=True,
        requested_notional_usd=10.0,
        venue_min_notional_usd=10.0,
        chosen_notional_usd=10.0,
        checklist={},
    )
    assert rec.get("proof_na_due_to_pre_execution_block") is not True
    assert rec.get("micro_validation_row_pass") is True
