"""Final pre-live closure: gap sweep, certification, authority, material change, activation exclusivity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_gap_sweep_and_certification_written(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    write_live_switch_closure_bundle(runtime_root=tmp_path, trigger_surface="test", reason="pytest")
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    assert ad.exists("data/control/final_system_gap_sweep.json")
    assert ad.exists("data/control/buy_sell_log_rebuy_certification.json")
    cert = ad.read_json("data/control/buy_sell_log_rebuy_certification.json") or {}
    assert cert.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") is False


def test_final_go_live_decision_engine_schema(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    write_live_switch_closure_bundle(runtime_root=tmp_path, trigger_surface="test", reason="pytest")
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    d = ad.read_json("data/control/final_go_live_decision.json") or {}
    assert d.get("FINAL_DECISION") in ("GO_LIVE_ALLOWED", "DO_NOT_GO_LIVE")
    assert "avenue_a_can_go_live_now" in d


def test_avenue_authority_no_cross_leakage(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    write_live_switch_closure_bundle(runtime_root=tmp_path, trigger_surface="test", reason="pytest")
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    auth = ad.read_json("data/control/final_avenue_readiness_authority.json") or {}
    avenues = auth.get("avenues") or {}
    assert avenues.get("A", {}).get("venue_mapping") == "Coinbase"
    assert avenues.get("B", {}).get("venue_mapping") == "Kalshi"
    assert avenues.get("C", {}).get("venue_mapping") == "Tastytrade"
    assert avenues.get("A", {}).get("no_cross_avenue_inheritance") is True


def test_activation_section_i_mutually_exclusive(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    write_live_switch_closure_bundle(runtime_root=tmp_path, trigger_surface="test", reason="pytest")
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    has_seq = ad.exists("data/control/avenue_a_final_safe_activation_sequence.json")
    has_blk = ad.exists("data/control/avenue_a_final_activation_blockers.json")
    assert has_seq != has_blk


def test_material_change_truth_v2(tmp_path: Path) -> None:
    from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    write_live_switch_closure_bundle(runtime_root=tmp_path, trigger_surface="test", reason="pytest")
    ad = LocalStorageAdapter(runtime_root=tmp_path)
    m = ad.read_json("data/control/runtime_material_change_truth.json") or {}
    assert "material_change_detected" in m
    assert m.get("closure_bundle_refreshed") is True
    assert ad.exists("data/control/_material_closure_fingerprints.json")


def test_final_pre_live_writers_certification_strict(tmp_path: Path) -> None:
    from trading_ai.orchestration.final_pre_live_writers import build_buy_sell_log_rebuy_certification
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    loop = {
        "final_execution_proven": True,
        "execution_lifecycle_state": "FINALIZED",
        "ready_for_rebuy": True,
        "partial_failure_flags": [],
        "lifecycle_stages": {
            "entry_fill_confirmed": True,
            "exit_fill_confirmed": True,
            "pnl_verified": True,
            "local_write_ok": True,
            "remote_write_ok": True,
            "governance_logged": True,
            "review_update_ok": True,
        },
        "bundle": {"remote_write": {"remote_required": True}},
    }
    (ctrl / "universal_execution_loop_proof.json").write_text(json.dumps(loop), encoding="utf-8")
    c = build_buy_sell_log_rebuy_certification(runtime_root=tmp_path)
    assert c.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") is True


def test_switch_live_c_blocker_string(tmp_path: Path) -> None:
    from trading_ai.orchestration.switch_live import compute_avenue_switch_live_now

    sw, bl, _ = compute_avenue_switch_live_now("C", runtime_root=tmp_path)
    assert sw is False
    assert any("tastytrade" in str(x).lower() or "scaffold" in str(x).lower() for x in bl)
