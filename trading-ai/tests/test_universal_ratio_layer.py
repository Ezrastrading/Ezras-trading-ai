"""Universal ratio registry, reserve, trade fold, gate views, audits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.nte.databank.databank_schema import (
    DATABANK_SCHEMA_VERSION,
    fold_ratio_context_into_merged,
    merge_defaults,
    row_for_supabase_trade_events,
)
from trading_ai.ratios.gate_ratio_access import gate_a_ratio_view, gate_b_ratio_view
from trading_ai.ratios.recent_work_activation import build_recent_work_activation_audit
from trading_ai.ratios.trade_ratio_context import build_ratio_context_for_trade_event
from trading_ai.ratios.universal_ratio_registry import build_universal_ratio_policy_bundle


def test_ratio_inheritance_no_silent_overwrite() -> None:
    b = build_universal_ratio_policy_bundle()
    u = float(b.universal_ratios["universal.per_trade_cap_fraction"]["value"])
    ga = float(b.gate_overlays["gate_a"]["gate.gate_a.per_trade_cap_fraction"]["value"])
    assert ga == u
    gb = float(
        b.gate_overlays["gate_b"]["gate.gate_b.momentum_safe_deployable_fraction"]["value"]
    )
    assert gb <= u + 1e-6


def test_universal_ratio_snapshot_dict_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.ratios.artifacts_writer import write_all_ratio_artifacts

    write_all_ratio_artifacts(runtime_root=tmp_path, append_change_log=False)
    snap = tmp_path / "data" / "control" / "ratio_policy_snapshot.json"
    assert snap.is_file()
    raw = json.loads(snap.read_text(encoding="utf-8"))
    assert raw.get("bundle", {}).get("ratio_policy_version")


def test_reserve_splits_from_deployable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    ctrl.joinpath("deployable_capital_report.json").write_text(
        json.dumps({"conservative_deployable_capital": 100.0, "portfolio_total_mark_value_usd": 500.0}),
        encoding="utf-8",
    )
    from trading_ai.ratios.artifacts_writer import write_all_ratio_artifacts
    from trading_ai.ratios.reserve_compute import build_reserve_capital_report

    b = build_universal_ratio_policy_bundle()
    write_all_ratio_artifacts(runtime_root=tmp_path, append_change_log=False)
    res = build_reserve_capital_report(bundle=b, control_dir=ctrl)
    assert res.get("reserved_capital_total", 0) > 0
    assert res.get("deployable_after_reserves", 0) < 100.0
    assert res.get("source_truth_status") == "sufficient"
    assert res.get("interpretation", {}).get("insufficient_source_truth") is False


def test_reserve_insufficient_source_truth_without_deployable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True)
    from trading_ai.ratios.reserve_compute import build_reserve_capital_report

    b = build_universal_ratio_policy_bundle()
    res = build_reserve_capital_report(bundle=b, control_dir=ctrl)
    assert res.get("source_truth_status") == "insufficient_source_truth"
    assert res.get("interpretation", {}).get("insufficient_source_truth") is True


def test_trade_event_ratio_context_folded_to_snapshot() -> None:
    raw = merge_defaults(
        {
            "trade_id": "t_test_ratio_1",
            "avenue_id": "A",
            "avenue_name": "coinbase",
            "asset": "BTC",
            "strategy_id": "s",
            "route_chosen": "A",
            "regime": "r",
            "timestamp_open": "2026-01-01T00:00:00Z",
            "timestamp_close": "2026-01-01T00:00:00Z",
            "ratio_context": {"ratio_policy_version": "universal_ratio_policy_v1", "x": 1},
        }
    )
    row = row_for_supabase_trade_events(raw, {})
    msj = row.get("market_snapshot_json")
    assert isinstance(msj, dict)
    assert msj.get("ratio_context", {}).get("ratio_policy_version")


def test_schema_version_bumped_for_ratio_context() -> None:
    assert DATABANK_SCHEMA_VERSION == "1.2.1"


def test_daily_ratio_review_and_mastery_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.ratios.daily_ratio_review import write_daily_ratio_review
    from trading_ai.ratios.system_mastery import write_last_48h_system_mastery

    write_daily_ratio_review(tmp_path)
    assert (tmp_path / "data" / "review" / "daily_ratio_review.json").is_file()
    write_last_48h_system_mastery(tmp_path)
    assert (tmp_path / "data" / "learning" / "last_48h_system_mastery.json").is_file()


def test_recent_work_activation_audit_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    aud = build_recent_work_activation_audit(runtime_root=tmp_path)
    assert aud.get("items")


def test_gate_views_return_keys() -> None:
    a = gate_a_ratio_view()
    b = gate_b_ratio_view()
    assert a.get("gate") == "A"
    assert b.get("gate") == "B"
    assert "reserve_policy" in a and "reserve_policy" in b


def test_trade_ratio_context_has_version() -> None:
    ctx = build_ratio_context_for_trade_event(
        trading_gate="gate_b",
        avenue_id="A",
        strategy_id="x",
    )
    assert ctx.get("ratio_policy_version")
