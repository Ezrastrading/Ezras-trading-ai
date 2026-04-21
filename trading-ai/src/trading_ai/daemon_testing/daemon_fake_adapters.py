"""LEVEL 1 — Pure fake adapters: deterministic outcomes; never counts as live proof."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.daemon_testing.contract import (
    AdapterTruthClass,
    DaemonMatrixRow,
    ExecutionMode,
    PassClassification,
    ProofStrength,
    proof_flags_for_row,
)
from trading_ai.daemon_testing.daemon_test_scenarios import fake_outcome_template
from trading_ai.daemon_testing.registry import GateBinding, AvenueBinding


def _tick_only_orders_allowed(mode: ExecutionMode) -> bool:
    return mode != "tick_only"


def build_fake_row(
    *,
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    execution_mode: ExecutionMode,
    adapter_truth_class: AdapterTruthClass = "fully_fake_adapter",
) -> DaemonMatrixRow:
    fo = fake_outcome_template(scenario_id)
    malformed = bool(fo.get("malformed_record"))
    cap_lie = bool(fo.get("capability_lie"))
    lock_cont = bool(fo.get("lock_contention"))

    avenue_wired = gate.live_execution_wired
    gate_harness = gate.gate_contract_wired_for_harness
    not_wired_live = bool(gate.not_wired_reason)

    # Capability lie / malformed: harness detects and fails closed
    if cap_lie or malformed:
        return _row_fail(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            "adapter_integrity_failure",
            avenue_wired,
            gate_harness,
            notes="fake_adapter_rejected_malformed_or_dishonest_capability",
        )

    if lock_cont:
        return _row_fail(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            "lock_contention",
            avenue_wired,
            gate_harness,
            notes="second_daemon_or_lock_contention_simulated",
        )

    if bool(fo.get("abort")):
        return _abort_row(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            str(fo.get("abort_reason") or "abort"),
            fo,
            avenue_wired,
            gate_harness,
        )

    allow_orders = _tick_only_orders_allowed(execution_mode)
    if execution_mode == "tick_only":
        # Tick: never place orders; still exercise "no candidate" etc. as truth-only
        return _tick_row(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            fo,
            adapter_truth_class,
            avenue_wired,
            gate_harness,
            not_wired_live,
        )

    if not allow_orders:
        return _row_fail(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            "mode_blocked",
            avenue_wired,
            gate_harness,
        )

    if not gate_harness:
        return _not_wired_row(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            avenue_wired,
        )

    # supervised vs autonomous: autonomous requires stricter invariants in fake (policy stub)
    if execution_mode == "autonomous_live" and not fo.get("gov_ok", True):
        return _row_fail(
            avenue,
            gate,
            scenario_id,
            scenario_title,
            execution_mode,
            adapter_truth_class,
            "governance_blocks_autonomous",
            avenue_wired,
            gate_harness,
        )

    return _live_path_row(
        avenue,
        gate,
        scenario_id,
        scenario_title,
        execution_mode,
        adapter_truth_class,
        fo,
        avenue_wired,
        gate_harness,
        not_wired_live,
    )


def _proof(adapter_truth_class: AdapterTruthClass, ok: bool, av: bool, g: bool) -> Dict[str, Any]:
    return proof_flags_for_row(
        adapter_truth_class=adapter_truth_class,
        pass_ok=ok,
        avenue_live_wired=av,
        gate_wired=g,
    )


def _not_wired_row(
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    mode: ExecutionMode,
    atc: AdapterTruthClass,
    avenue_wired: bool,
) -> DaemonMatrixRow:
    pf = _proof(atc, False, avenue_wired, gate.gate_contract_wired_for_harness)
    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode=mode,
        adapter_truth_class=atc,
        orders_attempted=False,
        entry_attempted=False,
        entry_filled=False,
        exit_attempted=False,
        exit_filled=False,
        pnl_verified=False,
        local_write_ok=False,
        remote_write_ok=False,
        governance_ok=False,
        review_ok=False,
        ready_for_rebuy=False,
        rebuy_attempted=False,
        rebuy_allowed=False,
        rebuy_block_reason="not_wired_for_this_gate_or_avenue",
        daemon_abort_triggered=False,
        final_state="NOT_WIRED",
        pass_classification="NOT_WIRED",
        proof_strength="none",
        blocking_reason=gate.not_wired_reason or "gate_contract_not_wired",
        notes="Harness refuses to simulate live steps on unwired gate — fake-only elsewhere.",
        fake_logic_proven=bool(pf.get("fake_logic_proven")),
        replay_logic_proven=bool(pf.get("replay_logic_proven")),
        live_proof_compatible=bool(pf.get("live_proof_compatible")),
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=avenue_wired,
        gate_contract_wired=gate.gate_contract_wired_for_harness,
    )


def _row_fail(
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    mode: ExecutionMode,
    atc: AdapterTruthClass,
    reason: str,
    avenue_wired: bool,
    gate_harness: bool,
    notes: str = "",
) -> DaemonMatrixRow:
    pf = _proof(atc, False, avenue_wired, gate_harness)
    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode=mode,
        adapter_truth_class=atc,
        orders_attempted=False,
        entry_attempted=False,
        entry_filled=False,
        exit_attempted=False,
        exit_filled=False,
        pnl_verified=False,
        local_write_ok=False,
        remote_write_ok=False,
        governance_ok=False,
        review_ok=False,
        ready_for_rebuy=False,
        rebuy_attempted=False,
        rebuy_allowed=False,
        rebuy_block_reason=reason,
        daemon_abort_triggered=False,
        final_state="FAILED",
        pass_classification="FAIL",
        proof_strength="fake_logic_only" if atc.startswith("fully") or atc == "venue_shaped_fake_adapter" else "none",
        blocking_reason=reason,
        notes=notes,
        fake_logic_proven=bool(pf.get("fake_logic_proven")),
        replay_logic_proven=bool(pf.get("replay_logic_proven")),
        live_proof_compatible=bool(pf.get("live_proof_compatible")),
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=avenue_wired,
        gate_contract_wired=gate_harness,
    )


def _abort_row(
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    mode: ExecutionMode,
    atc: AdapterTruthClass,
    reason: str,
    fo: Dict[str, Any],
    avenue_wired: bool,
    gate_harness: bool,
) -> DaemonMatrixRow:
    pf = _proof(atc, True, avenue_wired, gate_harness)
    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode=mode,
        adapter_truth_class=atc,
        orders_attempted=mode != "tick_only",
        entry_attempted=False,
        entry_filled=False,
        exit_attempted=False,
        exit_filled=False,
        pnl_verified=False,
        local_write_ok=False,
        remote_write_ok=False,
        governance_ok=False,
        review_ok=False,
        ready_for_rebuy=False,
        rebuy_attempted=False,
        rebuy_allowed=False,
        rebuy_block_reason=f"daemon_abort:{reason}",
        daemon_abort_triggered=True,
        final_state="ABORT",
        pass_classification="PASS",
        proof_strength="fake_logic_only",
        blocking_reason="",
        notes=f"Failure-stop path: {reason}",
        fake_logic_proven=bool(pf.get("fake_logic_proven")),
        replay_logic_proven=bool(pf.get("replay_logic_proven")),
        live_proof_compatible=bool(pf.get("live_proof_compatible")),
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=avenue_wired,
        gate_contract_wired=gate_harness,
        extra={"failure_injection": True, "blocked_live_step": True, "prevented_rebuy": True},
    )


def _tick_row(
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    fo: Dict[str, Any],
    atc: AdapterTruthClass,
    avenue_wired: bool,
    gate_harness: bool,
    not_wired_live: bool,
) -> DaemonMatrixRow:
    ok = True
    pf = _proof(atc, ok, avenue_wired, gate_harness)
    notes = "tick_only: no orders; scan/truth refresh only — does not prove supervised or autonomous live."
    if not fo.get("has_candidate", True):
        notes += " (no candidate in tick context)"
    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode="tick_only",
        adapter_truth_class=atc,
        orders_attempted=False,
        entry_attempted=False,
        entry_filled=False,
        exit_attempted=False,
        exit_filled=False,
        pnl_verified=False,
        local_write_ok=True,
        remote_write_ok=True,
        governance_ok=True,
        review_ok=True,
        ready_for_rebuy=False,
        rebuy_attempted=False,
        rebuy_allowed=False,
        rebuy_block_reason="tick_only_no_rebuy",
        daemon_abort_triggered=False,
        final_state="TICK_OK",
        pass_classification="PASS",
        proof_strength="fake_logic_only",
        blocking_reason="",
        notes=notes,
        fake_logic_proven=bool(pf.get("fake_logic_proven")),
        replay_logic_proven=bool(pf.get("replay_logic_proven")),
        live_proof_compatible=bool(pf.get("live_proof_compatible")),
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=avenue_wired,
        gate_contract_wired=gate_harness,
        extra={"tick_only_truth": True, "not_wired_live_context": not_wired_live},
    )


def _live_path_row(
    avenue: AvenueBinding,
    gate: GateBinding,
    scenario_id: str,
    scenario_title: str,
    mode: ExecutionMode,
    atc: AdapterTruthClass,
    fo: Dict[str, Any],
    avenue_wired: bool,
    gate_harness: bool,
    not_wired_live: bool,
) -> DaemonMatrixRow:
    has_c = bool(fo.get("has_candidate", True))
    pre = bool(fo.get("pretrade_block"))
    dup = bool(fo.get("duplicate_block"))
    es = bool(fo.get("entry_submit_ok", True))
    ef = bool(fo.get("entry_fill"))
    xs = bool(fo.get("exit_submit_ok", True))
    xf = bool(fo.get("exit_fill"))
    pnl = bool(fo.get("pnl_ok"))
    loc = bool(fo.get("local_ok"))
    rem = bool(fo.get("remote_ok", True))
    gov = bool(fo.get("gov_ok", True))
    rev = bool(fo.get("review_ok", True))
    rpol = bool(fo.get("rebuy_policy_ok", True))
    lessons = bool(fo.get("lessons_block"))
    adapt = bool(fo.get("adaptive_block"))
    log_complete = bool(fo.get("logging_complete", True))

    orders_attempted = has_c and not pre and not dup
    entry_attempted = orders_attempted and es
    entry_filled = entry_attempted and ef
    exit_attempted = entry_filled and xs
    exit_filled = exit_attempted and xf

    rebuy_block = ""
    ready_rebuy = False
    rebuy_allowed = False
    if entry_filled and exit_filled and pnl and loc and rem and gov and rev and log_complete:
        if rpol and not lessons and not adapt:
            ready_rebuy = True
            rebuy_allowed = scenario_id == "full_buy_sell_log_rebuy_eligible"
            rebuy_block = "" if rebuy_allowed else "scenario_not_rebuy_eligible_case"
        else:
            rebuy_block = (
                "policy"
                if not rpol
                else "lessons"
                if lessons
                else "adaptive"
                if adapt
                else "logging"
                if not log_complete
                else "remote"
                if not rem
                else "unknown"
            )
    elif scenario_id.startswith("rebuy_blocked"):
        rebuy_block = scenario_id.replace("rebuy_blocked_", "").replace("_", " ")

    final = "IN_FLIGHT"
    if fo.get("inflight_at_restart"):
        final = "INFLIGHT_AFTER_RESTART"
    elif fo.get("finalized_at_restart"):
        final = "FINALIZED_AFTER_RESTART"
    elif dup:
        final = "DUPLICATE_BLOCKED"
    elif pre:
        final = "PRETRADE_BLOCKED"
    elif not has_c:
        final = "NO_CANDIDATE"
    elif not es:
        final = "ENTRY_REJECTED"
    elif entry_filled and not xf:
        final = "ENTRY_FILLED_EXIT_OPEN"
    elif entry_filled and xf and pnl and loc:
        final = "ROUND_TRIP_LOGGED" if rem and gov and rev else "PARTIAL_TRUTH"
    else:
        final = "PARTIAL_OR_FAILED"

    ok = True
    if scenario_id in (
        "full_buy_sell_log",
        "full_buy_sell_log_rebuy_eligible",
        "daemon_restart_finalized",
    ):
        ok = entry_filled and exit_filled and pnl and loc and rem and gov and rev
    elif scenario_id == "entry_rejected_before_fill":
        ok = (not entry_filled) and (not es)
    elif scenario_id == "duplicate_blocked":
        ok = dup
    elif scenario_id == "candidate_blocked_pretrade":
        ok = pre
    elif scenario_id == "no_candidate":
        ok = not has_c
    elif scenario_id.startswith("rebuy_blocked"):
        ok = entry_filled and exit_filled and not rebuy_allowed
    elif scenario_id == "remote_sync_fail_after_local_success":
        ok = entry_filled and exit_filled and loc and (not rem)
    elif scenario_id == "governance_fail":
        ok = (not gov) and entry_filled
    elif scenario_id == "review_update_fail":
        ok = entry_filled and exit_filled and (not rev)
    elif scenario_id == "daemon_restart_inflight":
        ok = bool(fo.get("inflight_at_restart"))
    elif scenario_id.startswith("fi_"):
        ok = True  # failure rows validated in failure_injection artifact
    else:
        ok = True

    pc: PassClassification = "PASS" if ok else "FAIL"
    if not gate_harness:
        pc = "NOT_WIRED"
    if avenue_wired is False and mode in ("supervised_live", "autonomous_live"):
        # Live path not wired for this avenue — still allow PASS for fake logic with explicit note
        pc = "PASS" if ok else "FAIL"
        not_wired_live = True

    pf = _proof(atc, pc == "PASS", avenue_wired, gate_harness)
    notes = ""
    if not_wired_live:
        notes = (
            f"Production live not wired for this pair ({gate.not_wired_reason or 'policy'}) — "
            "fake path proves generic contract only."
        )

    return DaemonMatrixRow(
        avenue_id=avenue.avenue_id,
        avenue_name=avenue.display_name,
        gate_id=gate.gate_id,
        scenario_id=scenario_id,
        scenario_title=scenario_title,
        execution_mode=mode,
        adapter_truth_class=atc,
        orders_attempted=orders_attempted,
        entry_attempted=entry_attempted,
        entry_filled=entry_filled,
        exit_attempted=exit_attempted,
        exit_filled=exit_filled,
        pnl_verified=pnl,
        local_write_ok=loc,
        remote_write_ok=rem,
        governance_ok=gov,
        review_ok=rev,
        ready_for_rebuy=ready_rebuy,
        rebuy_attempted=scenario_id == "full_buy_sell_log_rebuy_eligible",
        rebuy_allowed=rebuy_allowed,
        rebuy_block_reason=rebuy_block,
        daemon_abort_triggered=False,
        final_state=final,
        pass_classification=pc,
        proof_strength="fake_logic_only",
        blocking_reason="" if ok else f"expectation_mismatch:{scenario_id}",
        notes=notes,
        fake_logic_proven=bool(pf.get("fake_logic_proven")),
        replay_logic_proven=bool(pf.get("replay_logic_proven")),
        live_proof_compatible=bool(pf.get("live_proof_compatible")),
        autonomous_live_runtime_proven=False,
        avenue_live_execution_wired=avenue_wired,
        gate_contract_wired=gate_harness,
        extra={"not_wired_live_context": not_wired_live},
    )
