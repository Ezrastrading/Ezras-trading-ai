"""
Avenue A (Coinbase / Gate A) live daemon — delegates orders to :func:`run_single_live_execution_validation`.

No shadow path: uses the same guarded live validation pipeline as operator-invoked proof runs.
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.orchestration.avenue_a_daemon_policy import (
    avenue_a_autonomous_live_allowed,
    avenue_a_autonomous_runtime_proven,
    avenue_a_daemon_mode,
    avenue_a_effective_autonomous_execution_tier,
    avenue_a_is_autonomous_family,
    avenue_a_supervised_runtime_allowed,
    min_consecutive_autonomous_cycles_required,
)
from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter
from trading_ai.global_layer.system_mission import MISSION_VERSION

_DAEMON_ACTIVE_ENV = "EZRAS_AVENUE_A_DAEMON_ACTIVE"
_STATE_REL = "data/control/avenue_a_daemon_state.json"
_TRUTH_REL = "data/control/avenue_a_daemon_live_truth.json"
_LAST_CYCLE_REL = "data/control/runtime_runner_last_cycle.json"


def _runtime_runner_cycle_envelope(out: Dict[str, Any], *, runtime_root: Path) -> Dict[str, Any]:
    """Every cycle record carries explicit runtime_root for cross-root safety and operator forensics."""
    return {
        "avenue_a_daemon": out,
        "ts": out["ts"],
        "runtime_root": str(Path(runtime_root).resolve()),
    }


def _min_interval_sec(mode: str) -> float:
    if mode == "supervised_live":
        return float((os.environ.get("EZRAS_AVENUE_A_SUPERVISED_MIN_INTERVAL_SEC") or "300").strip() or "300")
    if avenue_a_is_autonomous_family(mode):
        return float((os.environ.get("EZRAS_AVENUE_A_AUTONOMOUS_MIN_INTERVAL_SEC") or "60").strip() or "60")
    return 60.0


def _load_daemon_state(ad: LocalStorageAdapter) -> Dict[str, Any]:
    return ad.read_json(_STATE_REL) or {}


def _save_daemon_state(ad: LocalStorageAdapter, payload: Dict[str, Any]) -> None:
    ad.write_json(_STATE_REL, payload)


def _rebuy_allows_next_entry(runtime_root: Path) -> Tuple[bool, str]:
    from trading_ai.universal_execution.rebuy_policy import can_open_next_trade_after

    ad = LocalStorageAdapter(runtime_root=runtime_root)
    loop = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
    ls = loop.get("lifecycle_stages") or {}
    prior = {
        "final_execution_proven": loop.get("final_execution_proven"),
        "entry_fill_confirmed": ls.get("entry_fill_confirmed"),
        "exit_fill_confirmed": ls.get("exit_fill_confirmed"),
        "pnl_verified": ls.get("pnl_verified"),
        "local_write_ok": ls.get("local_write_ok"),
    }
    return can_open_next_trade_after(prior)


def _set_daemon_active(active: bool) -> None:
    if active:
        os.environ[_DAEMON_ACTIVE_ENV] = "1"
    else:
        os.environ.pop(_DAEMON_ACTIVE_ENV, None)


def run_avenue_a_daemon_tick_only(*, runtime_root: Path) -> Dict[str, Any]:
    """Scan/adaptive refresh only — no orders."""
    from trading_ai.orchestration.orchestration_truth import write_all_orchestration_artifacts
    from trading_ai.reports.gate_b_final_go_live_truth import write_gate_b_final_go_live_truth

    root = Path(runtime_root).resolve()
    write_all_orchestration_artifacts(runtime_root=root)
    write_gate_b_final_go_live_truth(runtime_root=root)
    return {"ok": True, "mode": "tick_only", "live_orders": False, "ts": datetime.now(timezone.utc).isoformat()}


def run_avenue_a_daemon_once(
    *,
    runtime_root: Optional[Path] = None,
    quote_usd: float = 10.0,
    product_id: str = "BTC-USD",
    include_runtime_stability: bool = True,
) -> Dict[str, Any]:
    """
    One Avenue A daemon cycle: live Gate A round-trip when mode is supervised or autonomous.
    """
    from trading_ai.orchestration.runtime_runner import daemon_abort_conditions
    from trading_ai.runtime_proof.live_execution_validation import run_single_live_execution_validation
    from trading_ai.shark.outlets.coinbase import CoinbaseAuthError

    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    mode = avenue_a_daemon_mode()
    ad = LocalStorageAdapter(runtime_root=root)
    t0 = time.perf_counter()
    out: Dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "runtime_root": str(root),
        "ts": datetime.now(timezone.utc).isoformat(),
        "duration_sec": 0.0,
        "live_orders": False,
        "armed_but_off": False,
        "autonomous_live_execution_enabled": False,
    }

    if mode == "disabled":
        out["skip_reason"] = "mode_disabled"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        return out

    if mode in ("tick_only", "paper_execution"):
        tick = run_avenue_a_daemon_tick_only(runtime_root=root)
        out.update(tick)
        out["ok"] = True
        out["note"] = "paper_execution_no_venue_orders" if mode == "paper_execution" else tick.get("note", "")
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_daemon_truth(runtime_root=root, last_cycle=out)
        _maybe_refresh_artifacts(runtime_root=root, reason=mode)
        return out

    abort, why, crit = daemon_abort_conditions(runtime_root=root)
    if abort:
        out["daemon_aborted"] = why
        out["critical"] = crit
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _record_failure_state(ad, out)
        _write_daemon_truth(runtime_root=root, last_cycle=out)
        return out

    if avenue_a_is_autonomous_family(mode):
        tier = avenue_a_effective_autonomous_execution_tier(runtime_root=root)
        out["autonomous_execution_tier"] = tier
        if tier == "armed_off":
            tick = run_avenue_a_daemon_tick_only(runtime_root=root)
            out.update(tick)
            out["ok"] = True
            out["armed_but_off"] = True
            out["autonomous_live_execution_enabled"] = False
            out["note"] = (
                "ARMED_BUT_OFF: autonomous daemon refresh/tick only — set data/control/autonomous_daemon_live_enable.json "
                "confirmed true AND EZRAS_AUTONOMOUS_DAEMON_LIVE_ENABLED=true for venue orders."
            )
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_daemon_truth(runtime_root=root, last_cycle=out)
            _maybe_refresh_artifacts(runtime_root=root, reason="autonomous_armed_but_off")
            return out
        live_ok, live_why = avenue_a_autonomous_live_allowed(runtime_root=root)
    elif mode == "supervised_live":
        live_ok, live_why = avenue_a_supervised_runtime_allowed(runtime_root=root)
    else:
        live_ok, live_why = False, f"unexpected_live_branch_mode:{mode}"
    if not live_ok:
        out["blocked"] = live_why
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        try:
            from trading_ai.orchestration.daemon_live_authority import write_daemon_last_gate_failure

            write_daemon_last_gate_failure(
                runtime_root=root,
                category="avenue_a_daemon_live_gate",
                detail=str(live_why),
                blockers=[str(live_why)],
            )
        except Exception:
            pass
        _write_daemon_truth(runtime_root=root, last_cycle=out)
        return out

    if avenue_a_is_autonomous_family(mode):
        from trading_ai.orchestration.autonomous_daemon_live_contract import autonomous_daemon_may_submit_live_orders

        dual_ok, dual_bl = autonomous_daemon_may_submit_live_orders(runtime_root=root)
        if not dual_ok:
            out["blocked"] = "autonomous_live_dual_gate:" + ";".join(dual_bl)
            out["armed_but_off"] = True
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_daemon_truth(runtime_root=root, last_cycle=out)
            return out
        out["autonomous_live_execution_enabled"] = True

    rb_ok, rb_why = _rebuy_allows_next_entry(root)
    if not rb_ok:
        out["blocked"] = f"rebuy_policy:{rb_why}"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_daemon_truth(runtime_root=root, last_cycle=out)
        return out

    if mode == "supervised_live":
        from trading_ai.nte.hardening.live_order_guard import validation_duplicate_isolation_key
        from trading_ai.safety.failsafe_guard import peek_duplicate_trade_window_would_block_entry

        dup_iso = validation_duplicate_isolation_key()
        if peek_duplicate_trade_window_would_block_entry(
            product_id=str(product_id),
            action="place_market_entry",
            gate="gate_a",
            runtime_root=root,
            validation_duplicate_isolation_key=dup_iso,
        ):
            out["ok"] = True
            out["skipped"] = True
            out["skip_reason"] = "duplicate_trade_window_active"
            out["skip_classification"] = "expected_guard_skip"
            out["live_orders"] = False
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            st = _load_daemon_state(ad)
            st["last_supervised_cycle_ok"] = True
            st["last_supervised_cycle_skipped"] = True
            st["last_supervised_skip_reason"] = "duplicate_trade_window_active"
            st["last_supervised_live_order_attempted"] = False
            st["last_mode"] = mode
            _save_daemon_state(ad, st)
            _write_daemon_truth(runtime_root=root, last_cycle=out, daemon_state=st)
            _maybe_refresh_artifacts(runtime_root=root, reason="avenue_a_daemon_supervised_duplicate_skip")
            try:
                from trading_ai.orchestration.avenue_a_daemon_artifacts import write_all_avenue_a_daemon_artifacts

                write_all_avenue_a_daemon_artifacts(runtime_root=root)
            except Exception as exc:
                out["artifact_bundle_error"] = str(exc)
            ad.write_json(_LAST_CYCLE_REL, _runtime_runner_cycle_envelope(out, runtime_root=root))
            return out

    out["live_orders"] = True
    _set_daemon_active(True)
    try:
        try:
            proof = run_single_live_execution_validation(
                root,
                quote_usd=float(quote_usd),
                product_id=str(product_id),
                include_runtime_stability=bool(include_runtime_stability),
                execution_profile="gate_a",
            )
        except CoinbaseAuthError as exc:
            from trading_ai.deployment.operator_env_contracts import coinbase_credentials_operator_hint

            _cb_hint = coinbase_credentials_operator_hint()
            proof = {
                "execution_success": False,
                "FINAL_EXECUTION_PROVEN": False,
                "error": f"coinbase_auth_failure:{exc}",
                "failure_stage": "pre_buy",
                "failure_code": _cb_hint.get("failure_code") or "coinbase_credentials_not_configured",
                "failure_reason": str(exc),
                "exact_missing_coinbase_env_vars": _cb_hint.get("exact_missing_coinbase_env_vars"),
                "next_step": _cb_hint.get("next_step"),
                "trade_id": None,
                "final_execution_proven": False,
                "coinbase_operator_env": _cb_hint,
                "venue_live_order_attempted": False,
                "honesty_note": (
                    "Structured failure for CoinbaseAuthError — no live order completed; fix credentials and re-run."
                ),
            }
    finally:
        _set_daemon_active(False)

    _lv_keys = (
        "execution_success",
        "error",
        "trade_id",
        "FINAL_EXECUTION_PROVEN",
        "failure_stage",
        "failure_code",
        "failure_reason",
        "exact_missing_coinbase_env_vars",
        "next_step",
        "final_execution_proven",
        "coinbase_operator_env",
        "venue_live_order_attempted",
        "honesty_note",
        "gate_a_selection_snapshot",
        "selected_product_source",
        "selection_snapshot_path",
        "selected_gate",
    )
    out["live_validation"] = {k: proof.get(k) for k in _lv_keys}
    if isinstance(proof.get("operator_supervised_live_gate_a"), dict):
        out["live_validation"]["operator_supervised_live_gate_a"] = proof["operator_supervised_live_gate_a"]
    if (os.environ.get("EZRAS_LOG_CAPITAL_GOVERNOR_DIAGNOSTICS") or "").strip().lower() in ("1", "true", "yes"):
        try:
            from trading_ai.global_layer.bot_registry import get_bot
            from trading_ai.global_layer.capital_governor import check_live_quote_allowed

            _bid = (os.environ.get("EZRAS_ACTIVE_ORCHESTRATION_BOT_ID") or "").strip()
            if _bid:
                _b = get_bot(_bid)
                if _b:
                    _ok, _why, _dg = check_live_quote_allowed(
                        _b,
                        float(quote_usd),
                        avenue="A",
                        gate="gate_a",
                        route="default",
                    )
                    out["capital_governor_diag"] = {"allowed": _ok, "reason": _why, "diagnostics": _dg}
        except Exception as exc:
            out["capital_governor_diag_error"] = str(exc)
    success = bool(proof.get("execution_success") and proof.get("FINAL_EXECUTION_PROVEN"))
    out["ok"] = success
    out["duration_sec"] = round(time.perf_counter() - t0, 4)

    if success and isinstance(proof.get("proof"), dict) and proof.get("proof"):
        from trading_ai.runtime_proof.live_execution_validation import persist_successful_gate_a_proof_to_disk

        persist_successful_gate_a_proof_to_disk(root, proof)

    st = _load_daemon_state(ad)
    if success:
        st["consecutive_autonomous_ok_cycles"] = int(st.get("consecutive_autonomous_ok_cycles") or 0) + 1
        if avenue_a_is_autonomous_family(mode):
            st["consecutive_autonomous_live_only_ok_cycles"] = int(st.get("consecutive_autonomous_live_only_ok_cycles") or 0) + 1
            _tid = str(proof.get("trade_id") or "").strip()
            if not _tid:
                _loop_early = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
                _tid = str(_loop_early.get("last_trade_id") or "").strip()
            st["last_counted_autonomous_cycle_ts"] = out["ts"]
            st["last_counted_autonomous_trade_id"] = _tid
            st["last_autonomous_cycle_counted_reason"] = "autonomous_family_daemon_cycle_success"
            st["last_autonomous_cycle_count_reset_reason"] = None
        else:
            st["consecutive_autonomous_live_only_ok_cycles"] = 0
            st["last_autonomous_cycle_count_reset_reason"] = f"non_autonomous_mode_success_does_not_count:{mode}"
        st["last_success_ts"] = out["ts"]
    else:
        st["consecutive_autonomous_ok_cycles"] = 0
        st["consecutive_autonomous_live_only_ok_cycles"] = 0
        st["last_failure_ts"] = out["ts"]
        st["last_autonomous_cycle_count_reset_reason"] = "cycle_failed_terminal_not_success"
    st["last_mode"] = mode
    if mode == "supervised_live":
        st["last_supervised_cycle_ok"] = bool(success)
        st["last_supervised_cycle_skipped"] = False
        st["last_supervised_skip_reason"] = None
        st["last_supervised_live_order_attempted"] = True
    _save_daemon_state(ad, st)

    from trading_ai.orchestration.avenue_a_daemon_failure_reporting import (
        build_daemon_cycle_terminal_fields,
        write_avenue_a_daemon_failure_truth,
    )
    from trading_ai.orchestration.daemon_live_authority import compute_env_fingerprint
    from trading_ai.universal_execution.gate_b_proof_bridge import try_emit_universal_loop_proof_from_gate_a_file

    emit = try_emit_universal_loop_proof_from_gate_a_file(runtime_root=root, overwrite_if_unproven=True)
    out["universal_loop_emit"] = emit

    terminal = build_daemon_cycle_terminal_fields(
        proof,
        emit,
        runtime_root=root,
        daemon_mode=mode,
        ts=out["ts"],
        product_id_arg=str(product_id),
    )
    out.update(terminal)

    write_avenue_a_daemon_failure_truth(
        runtime_root=root,
        terminal=terminal,
        success=bool(success),
        skipped=False,
    )

    if success:
        loop_snap = ad.read_json("data/control/universal_execution_loop_proof.json") or {}
        tid = str(loop_snap.get("last_trade_id") or proof.get("trade_id") or "")
        if tid:
            ad.write_json(
                "data/control/avenue_a_daemon_loop_emit_stamp.json",
                {
                    "truth_version": "avenue_a_daemon_loop_emit_stamp_v1",
                    "trade_id": tid,
                    "emitted_at": out["ts"],
                    "runtime_root": str(root),
                    "env_fingerprint_at_emit": compute_env_fingerprint(),
                    "execution_surface": "avenue_a_daemon",
                    "daemon_mode_at_emit": mode,
                    "emit_meta": {"universal_loop_emit": emit},
                },
            )

    _write_daemon_truth(runtime_root=root, last_cycle=out, daemon_state=st)
    _maybe_refresh_artifacts(runtime_root=root, reason="avenue_a_daemon_cycle")
    try:
        from trading_ai.orchestration.avenue_a_daemon_artifacts import write_all_avenue_a_daemon_artifacts

        write_all_avenue_a_daemon_artifacts(runtime_root=root)
    except Exception as exc:
        out["artifact_bundle_error"] = str(exc)

    ad.write_json(_LAST_CYCLE_REL, _runtime_runner_cycle_envelope(out, runtime_root=root))
    if success:
        ad.write_json("data/control/runtime_runner_last_success.json", {"avenue_a_daemon": out, "ts": out["ts"]})
    else:
        fail_body: Dict[str, Any] = {
            **terminal,
            "avenue_a_daemon": out,
            "ts": out["ts"],
            "runtime_root": str(root.resolve()),
        }
        if fail_body.get("failure_reason") is None:
            fail_body["failure_reason"] = proof.get("failure_reason") or proof.get("error") or "unknown_terminal_failure"
        ad.write_json("data/control/runtime_runner_last_failure.json", fail_body)
    return out


def _record_failure_state(ad: LocalStorageAdapter, summary: Dict[str, Any]) -> None:
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "summary": summary}, default=str)
    ad.ensure_parent("data/control/runtime_runner_failures.jsonl")
    with (ad.root() / "data/control/runtime_runner_failures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _write_daemon_truth(*, runtime_root: Path, last_cycle: Dict[str, Any], daemon_state: Optional[Dict[str, Any]] = None) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    st = daemon_state if daemon_state is not None else _load_daemon_state(ad)
    aut_p, aut_why = avenue_a_autonomous_runtime_proven(runtime_root=runtime_root)
    n_need = min_consecutive_autonomous_cycles_required()
    payload = {
        "truth_version": "avenue_a_daemon_live_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system_mission_version": MISSION_VERSION,
        "capital_utilization_mandate": "minimize_idle_notional_when_safe_per_rebuy_policy_and_governor",
        "daemon_mode": avenue_a_daemon_mode(),
        "AUTONOMOUS_DAEMON_RUNTIME_PROVEN": bool(aut_p),
        "autonomous_not_proven_reason": "" if aut_p else aut_why,
        "min_consecutive_cycles_required": n_need,
        "consecutive_autonomous_ok_cycles": int(st.get("consecutive_autonomous_ok_cycles") or 0),
        "last_cycle": last_cycle,
        "BUY_SELL_LOG_REBUY_RUNTIME_PROVEN": bool((ad.read_json("data/control/universal_execution_loop_proof.json") or {}).get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN")),
        "honesty": "AUTONOMOUS_DAEMON_RUNTIME_PROVEN requires verification JSON + consecutive cycles + finalized loop proof — not code presence.",
    }
    ad.write_json(_TRUTH_REL, payload)
    ad.write_text("data/control/avenue_a_daemon_live_truth.txt", json.dumps(payload, indent=2) + "\n")


def _maybe_refresh_artifacts(*, runtime_root: Path, reason: str) -> None:
    try:
        from trading_ai.reports.runtime_artifact_refresh_manager import run_refresh_runtime_artifacts

        run_refresh_runtime_artifacts(runtime_root=runtime_root, force=False, include_advisory=True)
    except Exception:
        pass
    try:
        from trading_ai.operator_truth.live_switch_closure_bundle import write_live_switch_closure_bundle

        write_live_switch_closure_bundle(runtime_root=runtime_root, trigger_surface="avenue_a_daemon", reason=reason)
    except Exception:
        pass


def run_avenue_a_daemon_forever(
    *,
    runtime_root: Optional[Path] = None,
    quote_usd: float = 10.0,
    product_id: str = "BTC-USD",
) -> None:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    stop = False

    def _sig(*_a: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    interval = _min_interval_sec(avenue_a_daemon_mode())
    while not stop:
        run_avenue_a_daemon_once(
            runtime_root=root,
            quote_usd=quote_usd,
            product_id=product_id,
            include_runtime_stability=True,
        )
        time.sleep(max(5.0, interval))


def avenue_a_daemon_status(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    JSON snapshot for operators: supervised readiness, autonomous tier, Gate A proof summary.

    **Consistency truth on disk:** calls :func:`trading_ai.orchestration.daemon_live_authority.build_daemon_runtime_consistency_truth`,
    which **recomputes and writes** ``data/control/daemon_runtime_consistency_truth.json`` (and ``.txt``) for the
    current process vs ``daemon_live_switch_authority.json``. The returned ``runtime_consistency`` fields match that
    written payload (same function, single source of truth). This is intentional so status reflects live process
    truth and the artifact stays aligned for forensics; it does not erase historical failure files elsewhere.
    """
    from trading_ai.orchestration.autonomous_daemon_live_contract import (
        autonomous_daemon_may_submit_live_orders,
        read_autonomous_daemon_live_enable,
    )
    from trading_ai.orchestration.avenue_a_daemon_policy import (
        avenue_a_effective_autonomous_execution_tier,
        avenue_a_supervised_runtime_allowed,
    )
    from trading_ai.deployment.operator_env_contracts import build_env_config_blocker_summary

    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    ad = LocalStorageAdapter(runtime_root=root)
    from trading_ai.orchestration.daemon_live_authority import build_daemon_runtime_consistency_truth

    auth_snap = ad.read_json("data/control/daemon_live_switch_authority.json") or {}
    cons = build_daemon_runtime_consistency_truth(
        runtime_root=root,
        stored_authority=auth_snap if auth_snap else None,
    )
    st = _load_daemon_state(ad)
    truth = ad.read_json(_TRUTH_REL) or {}
    lock = ad.root() / "data/control/runtime_runner.lock"
    dual_ok, dual_bl = autonomous_daemon_may_submit_live_orders(runtime_root=root)
    sup_ok, sup_why = avenue_a_supervised_runtime_allowed(runtime_root=root)
    sup_blockers = [x.strip() for x in str(sup_why).split(";") if x.strip()] if not sup_ok else []

    gate_a = ad.read_json("execution_proof/live_execution_validation.json") or {}
    den = ad.read_json("data/control/daemon_enable_readiness_after_supervised.json") or {}
    sup_live = ad.read_json("data/control/avenue_a_supervised_live_truth.json") or {}
    last_fail = ad.read_json("data/control/runtime_runner_last_failure.json")

    stale_notes: List[str] = []
    ga_ok = bool(gate_a.get("FINAL_EXECUTION_PROVEN") and gate_a.get("execution_success"))
    try:
        rr_p = str(gate_a.get("runtime_root") or "").strip()
        if rr_p and Path(rr_p).resolve() != root.resolve():
            stale_notes.append("gate_a_proof_runtime_root_mismatch_vs_status_runtime_root")
    except OSError:
        stale_notes.append("gate_a_proof_runtime_root_unresolvable")
    if not gate_a:
        stale_notes.append("no_execution_proof_live_execution_validation_json_on_disk")
    if ga_ok and last_fail and isinstance(last_fail, dict) and last_fail.get("avenue_a_daemon"):
        stale_notes.append(
            "historical_runtime_runner_last_failure_present_but_gate_a_proof_shows_current_success"
        )

    cons_ok = bool(cons.get("consistent_with_authoritative_artifacts"))
    cons_reason = str(cons.get("exact_do_not_run_reason_if_inconsistent") or "").strip()
    drift = [str(x) for x in (cons.get("env_fingerprint_drift_keys") or []) if x]
    refresh_cmd = (
        'cd "$(pwd)" && export PYTHONPATH=src && '
        f'export EZRAS_RUNTIME_ROOT="{root}" && '
        "python3 -m trading_ai.deployment refresh-supervised-daemon-truth-chain"
    )
    next_steps: List[str] = []
    if not cons_ok:
        next_steps.append(refresh_cmd)

    readiness_blockers: List[str] = []
    if not cons_ok and cons_reason:
        readiness_blockers.append("daemon_runtime_consistency:" + cons_reason)
    readiness_blockers.extend(sup_blockers)

    from trading_ai.orchestration.autonomous_operator_path import build_autonomous_operator_path

    autonomous_path_envelope = build_autonomous_operator_path(runtime_root=root)

    return {
        "mode": avenue_a_daemon_mode(),
        "runtime_root": str(root),
        "system_mission_version": MISSION_VERSION,
        "current_supervised_readiness_blockers": readiness_blockers,
        "current_autonomous_readiness_blockers": autonomous_path_envelope.get("active_blockers"),
        "historical_autonomous_artifact_notes": autonomous_path_envelope.get("historical_notes"),
        "raw_autonomous_reason_chain": autonomous_path_envelope.get("raw_autonomous_reason_chain"),
        "autonomous_blocker_debug": autonomous_path_envelope.get("autonomous_blocker_debug"),
        "supervised": {
            "can_run_supervised_now": bool(sup_ok),
            "supervised_daemon_enable_ready": bool(den.get("avenue_a_can_enable_daemon_now")),
            "supervised_live_runtime_proven_from_truth_file": bool(sup_live.get("supervised_live_runtime_proven")),
            "last_supervised_cycle_ok": st.get("last_supervised_cycle_ok"),
            "last_supervised_cycle_skipped": st.get("last_supervised_cycle_skipped"),
            "last_supervised_skip_reason": st.get("last_supervised_skip_reason"),
            "last_supervised_live_order_attempted": st.get("last_supervised_live_order_attempted"),
            "supervised_blockers_if_false": sup_blockers,
            "stale_or_historical_artifact_notes": stale_notes,
        },
        "autonomous": {
            "effective_autonomous_tier": avenue_a_effective_autonomous_execution_tier(runtime_root=root),
            "autonomous_live_enable_artifact": read_autonomous_daemon_live_enable(runtime_root=root),
            "dual_gate_live_orders_ok": dual_ok,
            "dual_gate_blockers_if_false": dual_bl,
        },
        "effective_autonomous_tier": avenue_a_effective_autonomous_execution_tier(runtime_root=root),
        "autonomous_live_enable_artifact": read_autonomous_daemon_live_enable(runtime_root=root),
        "dual_gate_live_orders_ok": dual_ok,
        "dual_gate_blockers_if_false": dual_bl,
        "daemon_state": st,
        "avenue_a_daemon_live_truth": truth,
        "runtime_runner_lock_present": lock.is_file(),
        "runtime_consistency": {
            "consistent_with_authoritative_artifacts": cons_ok,
            "exact_do_not_run_reason_if_inconsistent": cons_reason,
            "mismatched_keys": cons.get("mismatched_keys_or_surfaces") or cons.get("mismatched_keys") or [],
            "current_runtime_root": cons.get("current_runtime_root"),
            "current_env_fingerprint": cons.get("current_env_fingerprint"),
            "authority_fingerprint": cons.get("stored_authoritative_env_fingerprint")
            or auth_snap.get("authoritative_env_fingerprint"),
            "authority_runtime_root": auth_snap.get("authoritative_runtime_root"),
            "env_fingerprint_drift_keys": drift,
            "authority_refresh_recommended": not cons_ok,
            "refresh_authority_command": refresh_cmd,
            "canonical_operator_next_steps": next_steps,
        },
        "gate_a_live_execution_proof_snapshot": {
            "FINAL_EXECUTION_PROVEN": gate_a.get("FINAL_EXECUTION_PROVEN"),
            "execution_success": gate_a.get("execution_success"),
            "runtime_root_in_proof": gate_a.get("runtime_root"),
        },
        "ts": datetime.now(timezone.utc).isoformat(),
        "autonomous_path": autonomous_path_envelope,
        "operator_path": {
            "supervised_live": {
                "can_run_supervised_now": bool(sup_ok),
                "exact_blockers_if_false": sup_blockers,
            },
            "autonomous_live": {
                "dual_gate_live_orders_ok": dual_ok,
                "exact_blockers_if_false": dual_bl,
                "effective_autonomous_tier": avenue_a_effective_autonomous_execution_tier(runtime_root=root),
                "active_autonomous_blocker_summary": autonomous_path_envelope.get("deduped_blocker_chain_string"),
                "historical_autonomous_notes": autonomous_path_envelope.get("historical_notes"),
                "can_arm_autonomous_now": autonomous_path_envelope.get("can_arm_autonomous_now"),
                "why_not_one_sentence": autonomous_path_envelope.get("why_not_in_one_sentence"),
            },
            "env_config": build_env_config_blocker_summary(runtime_root=root, require_supervised_confirm=True),
            "runtime_consistency_green": cons_ok,
            "honesty": (
                "supervised blockers are policy/runtime gates; autonomous blockers are separate (dual gate + tier). "
                "env_config lists missing env names only — it does not validate key material."
            ),
        },
        "operator_path_summary": {
            "supervised": {"blockers": readiness_blockers},
            "autonomous": {
                "active_blockers": autonomous_path_envelope.get("active_blockers"),
                "historical_notes": autonomous_path_envelope.get("historical_notes"),
            },
        },
    }


def avenue_a_daemon_stop(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Best-effort: remove runner lock if our pid — operators normally SIGTERM the process."""
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    lock = root / "data/control/runtime_runner.lock"
    if lock.is_file():
        try:
            lock.unlink()
            return {"ok": True, "removed_lock": str(lock)}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "note": "no_lock_file"}
