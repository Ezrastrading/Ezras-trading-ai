"""Gate B staged parity: artifacts, strategy spec, reports (no live orders)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_gate_b_staged_validation_writes(rt: Path) -> None:
    from trading_ai.prelive.gate_b_staged_validation import run as gb_run

    gb_run(runtime_root=rt)
    assert (rt / "data/control/gate_b_staged_validation.json").is_file()
    assert (rt / "data/control/gate_b_scan_results.json").is_file()
    assert (rt / "data/control/gate_b_runtime_proof.json").is_file()
    assert (rt / "data/control/gate_a_gate_b_runtime_parity.json").is_file()
    assert (rt / "data/control/gate_b_validation.json").is_file()
    assert (rt / "data/control/gate_b_micro_validation_proof.json").is_file()
    assert (rt / "data/control/gate_a_gate_b_parity_matrix.json").is_file()
    assert (rt / "data/control/final_system_lock_status.json").is_file()
    assert (rt / "data/control/live_enablement_truth.json").is_file()
    assert (rt / "data/control/final_rerun_operator_pack.json").is_file()


def test_gainer_strict_entry(rt: Path) -> None:
    from trading_ai.shark.coinbase_spot.gate_b_strategy_spec import strict_entry_check

    ts = 1_700_000_000.0
    bad = {
        "product_id": "BAD-USD",
        "volume_24h_usd": 100.0,
        "spread_bps": 12.0,
        "book_depth_usd": 80_000.0,
        "move_pct": 0.08,
        "quote_ts": ts,
        "best_bid": 1.0,
        "best_ask": 1.01,
    }
    d = strict_entry_check(bad, open_product_ids=[])
    assert d.entry_pass is False


def test_future_avenue_proof(rt: Path) -> None:
    from trading_ai.prelive.future_avenue_auto_assignment_proof import run as fut_run

    fut_run(runtime_root=rt)
    assert (rt / "data/control/future_avenue_auto_assignment_proof.json").is_file()


def test_daily_master_report_writes(rt: Path) -> None:
    from trading_ai.reports.daily_master_operator_report import write_daily_master_operator_report

    write_daily_master_operator_report(runtime_root=rt)
    assert (rt / "data/reports/daily_master_operator_report.json").is_file()
