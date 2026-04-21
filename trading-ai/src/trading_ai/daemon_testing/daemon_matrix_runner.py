"""Run daemon verification matrix — rolls up to data/control artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from trading_ai.daemon_testing.contract import DaemonMatrixRow
from trading_ai.daemon_testing.daemon_fake_adapters import build_fake_row
from trading_ai.daemon_testing.daemon_replay_adapters import build_replay_row
from trading_ai.daemon_testing.daemon_test_scenarios import ALL_SCENARIOS
from trading_ai.daemon_testing.registry import iter_avenue_gate_pairs
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

ExecutionMode = Literal["tick_only", "supervised_live", "autonomous_live"]
LEVEL = Literal["fake", "replay", "live_proof"]


def _modes_for_level(level: LEVEL) -> Tuple[ExecutionMode, ...]:
    if level == "fake":
        return ("tick_only", "supervised_live", "autonomous_live")
    if level == "replay":
        return ("supervised_live",)
    return ("supervised_live",)


def _scenarios_for_live_proof() -> List[str]:
    return ["full_buy_sell_log", "full_buy_sell_log_rebuy_eligible"]


def evaluate_live_proof_compatibility_row(
    *,
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    runtime_root: Path,
) -> DaemonMatrixRow:
    """LEVEL 3 — validates control/proof files when present; does not place orders."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    base = build_fake_row(
        avenue=avenue,
        gate=gate,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode="supervised_live",
        adapter_truth_class="fully_fake_adapter",
    )
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    auth = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    cons = ad.read_json("data/control/daemon_runtime_consistency_truth.json") or {}
    proof_ref = ad.read_json("execution_proof/live_execution_validation.json")

    has_loop = isinstance(loop, dict) and bool(loop)
    has_auth = isinstance(auth, dict) and bool(auth.get("truth_version"))
    consistent = bool(cons.get("consistent_with_authoritative_artifacts"))
    has_live_proof_file = proof_ref is not None

    compatible = bool(has_loop and has_auth and consistent and has_live_proof_file and gate.live_execution_wired)
    notes = (
        f"live_proof_scan: loop={has_loop} authority={has_auth} consistency={consistent} "
        f"live_exec_json={has_live_proof_file} — does not prove autonomous live."
    )
    pc = "PASS" if compatible else "FAIL"
    if not gate.gate_contract_wired_for_harness:
        pc = "NOT_WIRED"

    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode="supervised_live",
        adapter_truth_class="real_runtime_proof_reference_present",
        orders_attempted=False,
        entry_attempted=False,
        entry_filled=False,
        exit_attempted=False,
        exit_filled=False,
        pnl_verified=bool(loop.get("final_execution_proven")) if has_loop else False,
        local_write_ok=bool((loop.get("lifecycle_stages") or {}).get("local_write_ok")) if has_loop else False,
        remote_write_ok=True,
        governance_ok=True,
        review_ok=True,
        ready_for_rebuy=False,
        rebuy_attempted=False,
        rebuy_allowed=False,
        rebuy_block_reason="live_proof_scan_only",
        daemon_abort_triggered=False,
        final_state="LIVE_PROOF_COMPAT_SCAN",
        pass_classification=pc,  # type: ignore[arg-type]
        proof_strength="live_proof_file_compatible_only",
        blocking_reason="" if compatible else "missing_or_incompatible_artifacts_for_route",
        notes=notes,
        fake_logic_proven=False,
        replay_logic_proven=False,
        live_proof_compatible=compatible,
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=gate.live_execution_wired,
        gate_contract_wired=gate.gate_contract_wired_for_harness,
        extra={"level": "live_proof_compatibility", "runtime_root": str(runtime_root)},
    )


def run_daemon_verification_matrix(
    *,
    runtime_root: Optional[Path] = None,
    levels: Optional[Sequence[LEVEL]] = None,
    replay_fixture: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    lv = tuple(levels or ("fake", "replay", "live_proof"))
    pairs = list(iter_avenue_gate_pairs(runtime_root=root))
    rows: List[DaemonMatrixRow] = []

    fixture = replay_fixture or (root / "tests" / "fixtures" / "daemon_replay" / "minimal_loop_proof.json")
    if not fixture.is_file():
        # packaged tests path when cwd is repo root
        alt = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "daemon_replay" / "minimal_loop_proof.json"
        if alt.is_file():
            fixture = alt

    for level in lv:
        if level == "fake":
            for av, g in pairs:
                for sc in ALL_SCENARIOS:
                    for mode in _modes_for_level("fake"):
                        rows.append(
                            build_fake_row(
                                avenue=av,
                                gate=g,
                                scenario_id=sc.scenario_id,
                                scenario_title=sc.title,
                                execution_mode=mode,
                                adapter_truth_class="fully_fake_adapter",
                            )
                        )
        elif level == "replay":
            for av, g in pairs:
                for sc in ALL_SCENARIOS:
                    for mode in _modes_for_level("replay"):
                        rows.append(
                            build_replay_row(
                                avenue=av,
                                gate=g,
                                scenario_id=sc.scenario_id,
                                scenario_title=sc.title,
                                execution_mode=mode,
                                replay_path=fixture,
                            )
                        )
        elif level == "live_proof":
            for av, g in pairs:
                for sid in _scenarios_for_live_proof():
                    st = next((s.title for s in ALL_SCENARIOS if s.scenario_id == sid), sid)
                    rows.append(
                        evaluate_live_proof_compatibility_row(
                            avenue=av,
                            gate=g,
                            scenario_id=sid,
                            scenario_title=st,
                            runtime_root=root,
                        )
                    )

    summary = _rollup_summary(rows)
    return {
        "truth_version": "daemon_verification_matrix_v1",
        "runtime_root": str(root),
        "levels_run": list(lv),
        "replay_fixture": str(fixture) if fixture else None,
        "row_count": len(rows),
        "rows": [r.to_json_dict() for r in rows],
        "summary": summary,
    }


def _rollup_summary(rows: Sequence[DaemonMatrixRow]) -> Dict[str, Any]:
    by_avenue: Dict[str, Dict[str, int]] = {}
    by_gate: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_avenue.setdefault(r.avenue_id, {"pass": 0, "fail": 0, "not_wired": 0, "skipped": 0})
        by_gate.setdefault(r.gate_id, {"pass": 0, "fail": 0, "not_wired": 0, "skipped": 0})
        k = str(r.pass_classification).lower()
        if k == "pass":
            by_avenue[r.avenue_id]["pass"] += 1
            by_gate[r.gate_id]["pass"] += 1
        elif k == "fail":
            by_avenue[r.avenue_id]["fail"] += 1
            by_gate[r.gate_id]["fail"] += 1
        elif k == "not_wired":
            by_avenue[r.avenue_id]["not_wired"] += 1
            by_gate[r.gate_id]["not_wired"] += 1
        else:
            by_avenue[r.avenue_id]["skipped"] += 1
            by_gate[r.gate_id]["skipped"] += 1

    fake_pass = sum(1 for r in rows if r.fake_logic_proven)
    replay_pass = sum(1 for r in rows if r.replay_logic_proven)
    live_c = sum(1 for r in rows if r.live_proof_compatible)
    not_wired = sum(1 for r in rows if r.pass_classification == "NOT_WIRED")
    failed = sum(1 for r in rows if r.pass_classification == "FAIL")
    skipped = sum(1 for r in rows if r.pass_classification == "SKIPPED")

    return {
        "total_scenarios_rows": len(rows),
        "passed_fake_flags": fake_pass,
        "passed_replay_flags": replay_pass,
        "live_proof_compatible_count": live_c,
        "not_wired_count": not_wired,
        "failed_count": failed,
        "skipped_count": skipped,
        "coverage_by_avenue": by_avenue,
        "coverage_by_gate": by_gate,
        "uncovered_critical_paths": _uncovered(rows),
    }


def _uncovered(rows: Sequence[DaemonMatrixRow]) -> List[str]:
    """Heuristic gaps — autonomous live rows never prove runtime without external stamp."""
    out: List[str] = []
    if not any(r.adapter_truth_class == "real_runtime_proof_reference_present" for r in rows):
        out.append("no_live_proof_compatibility_rows_run")
    if not any(r.scenario_id == "full_buy_sell_log_rebuy_eligible" and r.pass_classification == "PASS" for r in rows):
        out.append("rebuy_eligible_scenario_not_passing_in_matrix_run")
    return out


