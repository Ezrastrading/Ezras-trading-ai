"""Tests for deployment proof runners (mocked / tmp filesystem)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "ezras-runtime"
    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "deployment").mkdir(parents=True, exist_ok=True)
    (root / "data" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "data" / "review").mkdir(parents=True, exist_ok=True)
    (root / "data" / "control").mkdir(parents=True, exist_ok=True)
    (root / "shark" / "state").mkdir(parents=True, exist_ok=True)
    (root / "shark" / "nte" / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
    return root


def test_classify_postgrest_404_maps_to_table_or_project() -> None:
    from trading_ai.nte.databank.supabase_error_classify import classify_postgrest_exception

    class E(Exception):
        pass

    exc = E('{"message":"JSON object requested, multiple (or no) rows returned"} 404')
    out = classify_postgrest_exception(exc)
    assert out["category"] == "http_404_rest_route_or_table"
    assert out["fix_scope"] == "manual_migration_or_wrong_project_url"


def test_ezras_runtime_root_rejects_bare_tilde(monkeypatch) -> None:
    from trading_ai import runtime_paths

    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", "~")
    p = runtime_paths.ezras_runtime_root()
    assert "ezras-runtime" in str(p)
    assert not str(p).endswith("/~")


def test_checklist_fails_on_placeholder_env(monkeypatch, rt: Path) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://YOUR_PROJECT.supabase.co")
    from trading_ai.deployment.env_parity import run_env_parity_report

    rep = run_env_parity_report(write_file=False)
    assert rep.get("env_parity_ok") is False
    assert any("YOUR_" in str(x) for x in (rep.get("placeholders_found") or []))


def test_first_20_readiness_false_before_streak(rt: Path) -> None:
    from trading_ai.deployment.readiness_decision import compute_final_readiness

    with patch("trading_ai.deployment.readiness_decision.run_deployment_checklist") as m_cl:
        m_cl.return_value = {
            "ready_for_live_micro_validation": True,
            "blocking_reasons": [],
        }
        with patch("trading_ai.deployment.readiness_decision.run_env_parity_report") as m_env:
            m_env.return_value = {"env_parity_ok": True}
            with patch("trading_ai.deployment.readiness_decision.prove_governance_behavior") as m_g:
                m_g.return_value = {
                    "governance_proof_ok": True,
                    "governance_trading_permitted": True,
                }
                out = compute_final_readiness(write_files=False)
    assert out.get("ready_for_first_20") is False
    assert "live_validation_streak_not_passed" in (out.get("critical_blockers") or [])


def test_streak_stops_on_failed_run(monkeypatch, rt: Path) -> None:
    monkeypatch.setenv("LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM", "YES_I_UNDERSTAND_REAL_CAPITAL")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EZRAS_DRY_RUN", "false")

    from trading_ai.deployment import live_micro_validation as lmv

    calls = {"n": 0}

    def fake_checklist(**kwargs):
        return {"ready_for_live_micro_validation": True, "blocking_reasons": []}

    def fake_single(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "trade_id": "t1",
                "venue_product_id": "BTC-USD",
                "coinbase_order_verified": True,
                "buy_fill_confirmed": True,
                "sell_fill_confirmed": True,
                "base_quote_truth_ok": True,
                "local_write_evidence_ok": True,
                "execution_success": True,
                "supabase_synced": True,
                "governance_logged": True,
                "packet_updated": True,
                "oversell_risk": False,
                "partial_failure_codes": [],
                "pnl_calculation_verified": True,
                "order_id_buy": "b1",
                "order_id_sell": "s1",
                "pipeline": {
                    "trade_memory_updated": True,
                    "trade_events_appended": True,
                    "federated_includes_trade_id": True,
                },
            }
        return {"error": "fail", "trade_id": "t2"}

    def ok_recon(*a, **kw):
        return {"reconciliation_ok": True, "notes": []}

    def ok_supa(*a, **kw):
        return {"supabase_proof_ok": True}

    def fake_snap(pid: str = "BTC-USD", **kw):
        return {
            "product_id": pid,
            "validation_base_asset": "BTC",
            "validation_quote_asset": "USD",
            "exchange_base_qty": 0.0,
            "quote_available_usd": 100.0,
            "quote_available_usdc": 0.0,
            "quote_available_combined_usd": 100.0,
            "internal_base_qty": 0.0,
            "base_inventory_market_value_usd": None,
            "total_spot_equity_usd": None,
            "imported_inventory_baseline": False,
            "source": "test",
        }

    with patch.object(lmv, "run_deployment_checklist", side_effect=fake_checklist):
        with patch.object(lmv, "_preresolve_chosen_quote_usd", return_value=(10.0, "BTC-USD")):
            with patch.object(lmv, "snapshot_live_spot_ledger", side_effect=fake_snap):
                with patch.object(lmv, "run_single_live_execution_validation", side_effect=fake_single):
                    with patch.object(lmv, "prove_reconciliation_after_trade", side_effect=ok_recon):
                        with patch.object(lmv, "prove_supabase_write", side_effect=ok_supa):
                            with patch.object(lmv, "run_ops_outputs_bundle", return_value={}):
                                with patch.object(lmv, "write_spot_operator_snapshots", return_value={}):
                                    out = lmv.run_live_micro_validation_streak(n=3)
    assert out.get("live_validation_streak_passed") is False
    assert out.get("n_completed") == 2


def test_supabase_proof_fails_when_row_missing(monkeypatch, rt: Path) -> None:
    from trading_ai.deployment.supabase_proof import prove_supabase_write

    monkeypatch.setattr("trading_ai.deployment.supabase_proof.time.sleep", lambda *_: None)
    with patch(
        "trading_ai.deployment.supabase_proof.select_trade_event_exists_detail",
        return_value={"exists": False, "trade_id": "missing_id", "error": None},
    ):
        with patch("trading_ai.deployment.supabase_proof.flush_unsynced_trades") as fl:
            fl.return_value = {"remaining": 1, "flushed": 0}
            out = prove_supabase_write("missing_id", append_log=False)
    assert out.get("supabase_proof_ok") is False


def test_governance_proof_detects_mismatch(rt: Path) -> None:
    from trading_ai.deployment import governance_proof as gp

    with patch.object(gp, "load_joint_review_snapshot", return_value={"present": True, "empty": False}):
        with patch.object(gp, "_decide", return_value=(True, "dry")):
            with patch.object(gp, "check_new_order_allowed_full", return_value=(False, "blocked", {})):
                with patch.object(gp, "_joint_review_path", return_value=rt / "jr.json"):
                    out = gp.prove_governance_behavior(write_file=False)
    assert out.get("governance_proof_ok") is False


def test_reconciliation_fails_on_btc_mismatch(rt: Path) -> None:
    from trading_ai.deployment import reconciliation_proof as rp

    fake_accts = [
        {"currency": "BTC", "available_balance": {"value": "0.5"}},
    ]
    mock_cc = MagicMock()
    mock_cc.has_credentials.return_value = True
    mock_cc.list_all_accounts.return_value = fake_accts

    with patch.object(rp, "load_positions", return_value={"open_positions": []}):
        with patch("trading_ai.shark.outlets.coinbase.CoinbaseClient", return_value=mock_cc):
            out = rp.prove_reconciliation_after_trade({"product_id": "BTC-USD"}, append_log=False, btc_tolerance=1e-9)
    assert out.get("reconciliation_ok") is False


def test_ops_outputs_marks_missing_reports(rt: Path) -> None:
    from trading_ai.deployment.ops_outputs_proof import verify_ops_outputs_proof

    with patch("trading_ai.deployment.ops_outputs_proof.command_center_snapshot_path", return_value=rt / "missing.json"):
        with patch("trading_ai.deployment.ops_outputs_proof.command_center_report_path", return_value=rt / "missing.txt"):
            with patch("trading_ai.deployment.ops_outputs_proof.daily_diagnosis_path", return_value=rt / "missing_d.json"):
                with patch("trading_ai.deployment.ops_outputs_proof.ceo_daily_review_json_path", return_value=rt / "ceo.json"):
                    with patch("trading_ai.deployment.ops_outputs_proof.ceo_daily_review_txt_path", return_value=rt / "ceo.txt"):
                        with patch("trading_ai.deployment.ops_outputs_proof.trading_memory_path", return_value=rt / "mem.json"):
                            with patch("trading_ai.deployment.ops_outputs_proof.global_trade_events_path", return_value=rt / "te.jsonl"):
                                with patch("trading_ai.deployment.ops_outputs_proof.path_daily_summary", return_value=rt / "dts.json"):
                                    with patch("trading_ai.deployment.ops_outputs_proof.path_weekly_summary", return_value=rt / "wts.json"):
                                        with patch("trading_ai.deployment.ops_outputs_proof.checklist_json_path", return_value=rt / "cl.json"):
                                            with patch("trading_ai.deployment.ops_outputs_proof.ezras_runtime_root", return_value=rt):
                                                with patch("trading_ai.deployment.ops_outputs_proof.trade_logs_dir", return_value=rt / "tl"):
                                                    out = verify_ops_outputs_proof(write_file=False)
    assert out.get("ops_outputs_ok") is False


def test_checklist_blocks_without_supabase_schema_when_not_ready(monkeypatch, rt: Path) -> None:
    monkeypatch.setenv("SUPABASE_SCHEMA_CHECK_SKIP", "1")
    from trading_ai.deployment.deployment_checklist import run_deployment_checklist
    from trading_ai.deployment.deployment_models import CheckResult

    gov_ok = {
        "governance_proof_ok": True,
        "governance_trading_permitted": True,
        "governance_trading_block_reason": None,
        "full_check": {"allowed": True, "reason": "ok", "audit_keys": []},
        "joint_snapshot_summary": {},
    }
    with patch("trading_ai.deployment.deployment_checklist._exchange_auth", return_value=CheckResult(True, "ok", {})):
        with patch("trading_ai.deployment.deployment_checklist.prove_governance_behavior", return_value=gov_ok):
            with patch("trading_ai.deployment.deployment_checklist._supabase_check", return_value=CheckResult(True, "ok", {})):
                with patch("trading_ai.deployment.deployment_checklist.run_supabase_schema_readiness") as sch:
                    sch.return_value = {
                        "schema_ready": False,
                        "supabase_schema_ready": False,
                        "blocking_reasons": ["remote_schema_mismatch"],
                    }
                    with patch("trading_ai.deployment.deployment_checklist.run_deployment_parity_report") as dp:
                        dp.return_value = {"deployment_parity_ready": True, "blocking_reasons": []}
                        with patch("trading_ai.deployment.deployment_checklist._reconciliation_check", return_value=CheckResult(True, "ok", {})):
                            with patch("trading_ai.deployment.deployment_checklist._validation_streak_ready", return_value=CheckResult(True, "ok", {})):
                                with patch(
                                    "trading_ai.deployment.deployment_checklist.evaluate_first_20_protocol_readiness",
                                    return_value={"first_20_protocol_ready": True, "reasons": []},
                                ):
                                    with patch(
                                        "trading_ai.deployment.deployment_checklist.run_env_parity_report",
                                        return_value={"env_parity_ok": True, "blocking_reasons": []},
                                    ):
                                        with patch("trading_ai.deployment.deployment_checklist._soak_ready", return_value=CheckResult(True, "ok", {})):
                                            with patch("trading_ai.deployment.deployment_checklist._observability_check", return_value=CheckResult(True, "ok", {})):
                                                out = run_deployment_checklist(write_files=False)
    assert out.get("ready_for_live_micro_validation") is False
    assert any("supabase_schema" in x for x in (out.get("blocking_reasons") or []))
