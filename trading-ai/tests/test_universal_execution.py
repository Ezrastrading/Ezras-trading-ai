"""Universal execution truth contract, rebuy policy, adapters, proof strictness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from trading_ai.universal_execution.avenue_adapter import AdapterContext, AvenueAdapterBase, AvenueCapabilityGap
from trading_ai.universal_execution.execution_truth_contract import ExecutionTruthContract, ExecutionTruthStage
from trading_ai.universal_execution.rebuy_policy import can_open_next_trade_after
from trading_ai.universal_execution.universal_execution_proof import build_universal_execution_proof_payload
from trading_ai.universal_execution.universal_execution_loop_proof import build_universal_execution_loop_proof_payload
from trading_ai.universal_execution.universal_trade_cycle import execute_round_trip_with_truth
from trading_ai.reports.lessons_runtime_truth import build_lessons_runtime_truth
from trading_ai.universal_execution.adapters import CoinbaseAvenueAdapter, KalshiAvenueAdapter, TastytradeAvenueAdapter
from trading_ai.universal_execution.runtime_truth_material_change import refresh_runtime_truth_after_material_change
from trading_ai.universal_execution.adaptive_truth_classification import LiveEntryBlockerClass


class _FakeOkAdapter(AvenueAdapterBase):
    avenue_id = "T"
    avenue_name = "test"

    def capability_gaps(self) -> List[AvenueCapabilityGap]:
        return []

    def scan_candidates(self, ctx: AdapterContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return [{"id": "c1"}], {}

    def select_candidate(
        self, ctx: AdapterContext, candidates: List[Dict[str, Any]]
    ) -> Tuple[Any, Dict[str, Any]]:
        return (candidates[0] if candidates else None), {}

    def pretrade_validate(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"blocking_reason": None}

    def submit_entry(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"order_id": "e1", "proof_source": "test", "proof_kind": "mock"}

    def confirm_entry_fill(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"confirmed": True, "truth_source": "mock_fills", "proof_kind": "fills"}

    def compute_exit_plan(self, ctx: AdapterContext, entry_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return {"exit_reason": "profit_target"}, {}

    def submit_exit(self, ctx: AdapterContext, exit_plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"order_id": "x1", "proof_source": "test", "proof_kind": "mock"}

    def confirm_exit_fill(self, ctx: AdapterContext, exit_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"confirmed": True, "truth_source": "mock_fills", "proof_kind": "fills"}

    def compute_realized_pnl(
        self, ctx: AdapterContext, entry_meta: Dict[str, Any], exit_meta: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return {"complete": True, "net_pnl": 1.23, "gross_pnl": 2.0}, {}

    def build_trade_record(
        self,
        ctx: AdapterContext,
        *,
        entry_meta: Dict[str, Any],
        exit_meta: Dict[str, Any],
        pnl_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "trade_id": "t1",
            "avenue_id": self.avenue_id,
            "net_pnl": pnl_block.get("net_pnl"),
            "entry_fill_confirmed": True,
            "exit_fill_confirmed": True,
        }

    def append_local_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"ok": True}

    def upsert_remote_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"remote_required": False, "ok": True}

    def refresh_summaries(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return True, {"ok": True}

    def produce_execution_proof(self, ctx: AdapterContext, bundle: Dict[str, Any]) -> Dict[str, Any]:
        return {"scheduler_stable": True}


def test_stage_prerequisite_enforced() -> None:
    c = ExecutionTruthContract()
    c.set_stage(ExecutionTruthStage.STAGE_0_CANDIDATE_SELECTED, ok=True, avenue_id="A", proof_source="t", proof_kind="t")
    with pytest.raises(ValueError, match="prerequisite"):
        c.set_stage(ExecutionTruthStage.STAGE_2_ENTRY_ORDER_SUBMITTED, ok=True, avenue_id="A", proof_source="t", proof_kind="t")


def test_rebuy_blocked_entry_filled_exit_unverified() -> None:
    ok, why = can_open_next_trade_after({"entry_fill_confirmed": True, "exit_fill_confirmed": False})
    assert ok is False
    assert "exit" in why


def test_rebuy_allowed_entry_failed_logged() -> None:
    ok, why = can_open_next_trade_after(
        {"terminal_honest_state": "entry_failed_pre_fill", "entry_fill_confirmed": False}
    )
    assert ok is True


def test_final_execution_proof_strict() -> None:
    ad = _FakeOkAdapter()
    out = execute_round_trip_with_truth(ad, ctx=AdapterContext(avenue_id="T"))
    assert out["cycle_ok"] is True
    assert out["final_execution_proven"] is True
    proof = out["bundle"]["universal_proof"]
    assert proof["final_execution_proven"] is True
    assert proof["databank_written"] is True


def test_final_proof_false_if_incomplete() -> None:
    c = ExecutionTruthContract()
    bundle = {
        "entry_fill": {"confirmed": True},
        "exit_fill": {"confirmed": False},
        "normalized_trade_record": {"net_pnl": 1.0},
    }
    p = build_universal_execution_proof_payload(bundle, c)
    assert p["final_execution_proven"] is False


def test_coinbase_kalshi_tastytrade_adapters_report_gaps() -> None:
    assert CoinbaseAvenueAdapter().capability_gaps()[0].blocks_live_orders is True
    assert KalshiAvenueAdapter().capability_gaps()[0].code.startswith("not_yet")
    assert TastytradeAvenueAdapter().capability_gaps()[0].blocks_live_orders is True


def test_lessons_extended_fields_present() -> None:
    p = build_lessons_runtime_truth()
    assert "lessons_influence_candidate_selection" in p
    assert p.get("lessons_not_used_by_live_order_path") is True
    assert p.get("exact_wiring_needed_for_lessons_to_influence_live_decisions")


def test_refresh_runtime_truth_after_material_change_smoke() -> None:
    out = refresh_runtime_truth_after_material_change(reason="test", force=False)
    assert "refresh_runtime_truth_reason" in out
    assert "artifacts_refreshed" in out or "artifacts_skipped_as_fresh" in str(out)


def test_live_entry_blocker_classification_values() -> None:
    assert LiveEntryBlockerClass.STALE_PERSISTED_NON_AUTHORITATIVE.value == "stale_persisted_non_authoritative"


class _BadExitFillAdapter(_FakeOkAdapter):
    def confirm_exit_fill(self, ctx: AdapterContext, exit_meta: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"confirmed": False, "blocking_reason": "no_exit_fill"}


class _BadLocalWriteAdapter(_FakeOkAdapter):
    def append_local_trade_event(self, ctx: AdapterContext, record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"ok": False}


class _DupPretradeAdapter(_FakeOkAdapter):
    def pretrade_validate(self, ctx: AdapterContext, candidate: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"blocking_reason": "duplicate_order_attempt"}


def test_exit_fill_failure_blocks_rebuy_in_loop_proof() -> None:
    out = execute_round_trip_with_truth(_BadExitFillAdapter(), ctx=AdapterContext(avenue_id="T"))
    assert out["final_execution_proven"] is False
    lp = build_universal_execution_loop_proof_payload(out)
    assert lp["ready_for_rebuy"] is False
    assert lp["lifecycle_stages"]["exit_fill_confirmed"] is False


def test_local_write_failure_blocks_final_execution() -> None:
    out = execute_round_trip_with_truth(_BadLocalWriteAdapter(), ctx=AdapterContext(avenue_id="T"))
    assert out["final_execution_proven"] is False
    lp = build_universal_execution_loop_proof_payload(out)
    assert lp["lifecycle_stages"]["local_write_ok"] is False


def test_duplicate_pretrade_blocks_with_duplicate_terminal() -> None:
    out = execute_round_trip_with_truth(_DupPretradeAdapter(), ctx=AdapterContext(avenue_id="T"))
    assert out["terminal_honest_state"] == "duplicate_blocked"


def test_loop_proof_artifact_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    # Post-trade truth chain requires minimal on-disk artifacts when ``runtime_root`` is wired.
    (tmp_path / "execution_proof").mkdir(parents=True)
    (tmp_path / "execution_proof" / "execution_proof.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "pnl").mkdir(parents=True)
    (tmp_path / "data" / "pnl" / "pnl_record.json").write_text(
        json.dumps({"gross_pnl": 1.0, "fees": 0.1, "slippage": 0.0, "net_pnl": 0.9}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "risk").mkdir(parents=True)
    (tmp_path / "data" / "risk" / "risk_state.json").write_text(
        json.dumps({"status": "ACTIVE"}),
        encoding="utf-8",
    )
    out = execute_round_trip_with_truth(
        _FakeOkAdapter(),
        ctx=AdapterContext(avenue_id="T", extra={"runtime_root": tmp_path}),
    )
    p = tmp_path / "data" / "control" / "universal_execution_loop_proof.json"
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN") is True
    assert data.get("final_execution_proven") is True


def test_partial_failure_flags_invalidate_universal_proof() -> None:
    c = ExecutionTruthContract()
    bundle: Dict[str, Any] = {
        "entry_fill": {"confirmed": True},
        "exit_fill": {"confirmed": True},
        "normalized_trade_record": {"net_pnl": 1.0},
        "remote_write": {"remote_required": False},
        "partial_failure_flags": ["forced_test_flag"],
    }
    for i in range(11):
        c.set_stage(ExecutionTruthStage(i), ok=True, avenue_id="T", proof_source="t", proof_kind="t")  # type: ignore[arg-type]
    p = build_universal_execution_proof_payload(bundle, c)
    assert p["final_execution_proven"] is False
