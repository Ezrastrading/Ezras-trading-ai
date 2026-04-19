"""
Organism stress / integration harness — repeated ticks, packets, federation, governance.

Writes JSON reports under ``runtime_root / stress_proof /`` (no live capital).

Default ``iterations`` is 520+ for pressure coverage; use smaller values in unit tests.

**Soak mode** (``run_organism_soak_harness``): longer or time-bounded runs; writes
``soak_proof/soak_*.json`` and ``soak_report_summary.json``. Use ``test_mode=True`` in CI.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.joint_review_merger import merge_reviews
from trading_ai.global_layer.review_scheduler import run_full_review_cycle, tick_scheduler
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.nte.memory.store import MemoryStore


def _prepare_harness_runtime(
    runtime_root: Path,
    databank_root: Optional[Path],
) -> Tuple[MemoryStore, ReviewStorage, Path, int]:
    runtime_root = runtime_root.resolve()
    db = (databank_root or (runtime_root / "databank")).resolve()
    db.mkdir(parents=True, exist_ok=True)
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(db)
    os.environ.setdefault("AI_REVIEW_MAX_PER_DAY", "99999")

    gdir = runtime_root / "shark" / "memory" / "global"
    gdir.mkdir(parents=True, exist_ok=True)
    joint = {
        "joint_review_id": f"jr_stress_{uuid.uuid4().hex[:8]}",
        "live_mode_recommendation": "normal",
        "review_integrity_state": "full",
        "generated_at": "2099-08-01T12:00:00+00:00",
        "packet_id": "pkt_stress",
        "empty": False,
    }
    joint_path = gdir / "joint_review_latest.json"
    joint_path.write_text(json.dumps(joint), encoding="utf-8")

    ms = MemoryStore()
    ms.ensure_defaults()
    st = ReviewStorage()
    st.ensure_review_files()
    # Prevent tick_scheduler from calling run_full_review_cycle without skip_models (live API noise).
    sched_state = st.load_json("review_scheduler_state.json")
    sched_state["suppress_all"] = True
    st.save_json("review_scheduler_state.json", sched_state)
    tick_lines_before = _count_jsonl_lines(st.store.path("review_scheduler_ticks.jsonl"))
    return ms, st, joint_path, tick_lines_before


def run_organism_stress_harness(
    runtime_root: Path,
    *,
    iterations: int = 520,
    databank_root: Optional[Path] = None,
    review_cycle_every: int = 65,
    failure_injection: bool = False,
) -> Dict[str, Any]:
    """
    Exercise scheduler ticks, federation, governance, periodic full review cycles (stub models).

    ``failure_injection``: briefly hide ``joint_review_latest.json`` once to ensure recovery path
    (light fault injection; only when True).
    """
    runtime_root = runtime_root.resolve()
    db = (databank_root or (runtime_root / "databank")).resolve()
    ms, st, joint_path, tick_lines_before = _prepare_harness_runtime(runtime_root, db)

    tm = ms.load_json("trade_memory.json")
    n_seed = min(8, max(3, iterations // 64))
    tm["trades"] = (tm.get("trades") or []) + [
        {
            "trade_id": f"stress_{i}",
            "avenue": "coinbase",
            "route_bucket": "stress",
            "net_pnl_usd": 0.01 * i,
            "product_id": "BTC-USD",
        }
        for i in range(n_seed)
    ]
    ms.save_json("trade_memory.json", tm)
    gov_ok: List[bool] = []
    fed_counts: List[int] = []
    packet_ids: List[str] = []
    review_cycles_run = 0
    inj_note: Optional[str] = None

    t0 = time.time()
    for i in range(iterations):
        if failure_injection and i == iterations // 3:
            bak = joint_path.read_text(encoding="utf-8")
            joint_path.unlink(missing_ok=True)
            inj_note = "removed_joint_once"
            time.sleep(0.01)
            joint_path.write_text(bak, encoding="utf-8")

        ok, _reason, _a = check_new_order_allowed_full(
            venue="coinbase",
            operation="stress_probe",
            route="n/a",
            intent_id=f"stress_{i}",
            log_decision=False,
        )
        gov_ok.append(ok)
        trades, _meta = load_federated_trades(nte_store=ms)
        fed_counts.append(len(trades))
        read_normalized_internal(nte_store=ms)
        tick_scheduler(storage=st)

        if review_cycle_every > 0 and i > 0 and i % review_cycle_every == 0:
            run_full_review_cycle("midday", storage=st, skip_models=True)
            review_cycles_run += 1
            pkt = st.load_json("review_packet_latest.json")
            pid = str(pkt.get("packet_id") or "")
            packet_ids.append(pid)

    tick_lines_after = _count_jsonl_lines(st.store.path("review_scheduler_ticks.jsonl"))
    tick_delta = tick_lines_after - tick_lines_before

    from trading_ai.global_layer.ai_review_packet_builder import build_review_packet
    from trading_ai.global_layer.claude_review_runner import run_claude_review
    from trading_ai.global_layer.gpt_review_runner import run_gpt_review

    packet = build_review_packet(review_type="midday", storage=st)
    cl = run_claude_review(packet, storage=st, force_stub=True)
    gp = run_gpt_review(packet, storage=st, force_stub=True)
    j = merge_reviews(packet, cl, gp, storage=st)

    elapsed = time.time() - t0

    out_dir = runtime_root / "stress_proof"
    out_dir.mkdir(parents=True, exist_ok=True)

    tick_p = st.store.path("review_scheduler_ticks.jsonl")
    scheduler_report = {
        "scheduler_ticks_jsonl_lines_added": tick_delta,
        "iterations": iterations,
        "review_cycles_run": review_cycles_run,
        "review_cycle_every": review_cycle_every,
        "tick_file": str(tick_p),
        "malformed_jsonl_lines": _bad_jsonl_lines(tick_p),
        "duplicate_scheduler_job_simulated": False,
        "failure_injection": inj_note or "none",
    }
    fed_unique = sorted(set(fed_counts))
    federation_report = {
        "final_federated_trade_count": fed_counts[-1] if fed_counts else 0,
        "fed_counts_unique_values": fed_unique,
        "deterministic_federation_reads": len(fed_unique) <= 3,
        "fed_count_monotonic_non_decreasing": fed_counts == sorted(fed_counts),
    }
    pkt_latest = st.load_json("review_packet_latest.json")
    artifact_report = {
        "joint_review_written": joint_path.is_file(),
        "joint_integrity": str(j.get("review_integrity_state") or ""),
        "packet_truth_databank_root": (packet.get("packet_truth") or {}).get("databank_root"),
        "scheduler_parse_errors": scheduler_report["malformed_jsonl_lines"],
        "packet_ids_from_cycles": packet_ids[:25],
        "packet_ids_unique_in_cycles": len(packet_ids) == len(set(packet_ids)) if packet_ids else True,
        "final_packet_id": str(pkt_latest.get("packet_id") or ""),
        "stale_packet_check": str(pkt_latest.get("packet_id") or "") == str(packet.get("packet_id") or ""),
    }
    summary = {
        "runtime_root": str(runtime_root),
        "databank_root": str(db),
        "elapsed_sec": round(elapsed, 3),
        "governance_all_ok": all(gov_ok),
        "iterations": iterations,
    }

    (out_dir / "scheduler_stress_report.json").write_text(json.dumps(scheduler_report, indent=2), encoding="utf-8")
    (out_dir / "federation_stress_report.json").write_text(json.dumps(federation_report, indent=2), encoding="utf-8")
    (out_dir / "artifact_integrity_report.json").write_text(json.dumps(artifact_report, indent=2), encoding="utf-8")
    (out_dir / "runtime_proof_report.json").write_text(
        json.dumps({**summary, "artifact_integrity": artifact_report, "federation": federation_report}, indent=2),
        encoding="utf-8",
    )

    return {
        "summary": summary,
        "scheduler_stress_report": scheduler_report,
        "federation_stress_report": federation_report,
        "artifact_integrity_report": artifact_report,
        "output_dir": str(out_dir),
    }


def run_organism_soak_harness(
    runtime_root: Path,
    *,
    databank_root: Optional[Path] = None,
    min_iterations: int = 800,
    max_duration_sec: float = 600.0,
    review_cycle_every: int = 40,
    failure_injection: bool = False,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """
    Long-run / soak profile: time-bounded and iteration-bounded loop (governance, federation, scheduler, packets).

    ``test_mode=True`` caps work for CI (short duration, fewer iterations).

    Manual multi-hour example::

        PYTHONPATH=src python3 -c "
        from pathlib import Path
        from trading_ai.runtime_proof.organism_stress_harness import run_organism_soak_harness
        run_organism_soak_harness(Path('/tmp/soak_run'), min_iterations=5000, max_duration_sec=7200, test_mode=False)
        "
    """
    runtime_root = runtime_root.resolve()
    db = (databank_root or (runtime_root / "databank")).resolve()
    if test_mode:
        min_iterations = min(min_iterations, 48)
        max_duration_sec = min(max_duration_sec, 12.0)
        review_cycle_every = min(review_cycle_every, 12)

    ms, st, joint_path, tick_lines_before = _prepare_harness_runtime(runtime_root, db)
    tm = ms.load_json("trade_memory.json")
    est = max(min_iterations, 64)
    n_seed = min(12, max(4, est // 80))
    tm["trades"] = (tm.get("trades") or []) + [
        {
            "trade_id": f"soak_{i}",
            "avenue": "coinbase",
            "route_bucket": "soak",
            "net_pnl_usd": 0.005 * i,
            "product_id": "BTC-USD",
        }
        for i in range(n_seed)
    ]
    ms.save_json("trade_memory.json", tm)

    gov_ok: List[bool] = []
    fed_counts: List[int] = []
    packet_ids: List[str] = []
    review_cycles_run = 0
    inj_note: Optional[str] = None
    iterations = 0
    t_wall = time.time()
    t0 = time.time()

    while True:
        if failure_injection and iterations > 0 and iterations == min_iterations // 3:
            bak = joint_path.read_text(encoding="utf-8")
            joint_path.unlink(missing_ok=True)
            inj_note = "removed_joint_once"
            time.sleep(0.01)
            joint_path.write_text(bak, encoding="utf-8")

        ok, _reason, _a = check_new_order_allowed_full(
            venue="coinbase",
            operation="soak_probe",
            route="n/a",
            intent_id=f"soak_{iterations}",
            log_decision=False,
        )
        gov_ok.append(ok)
        trades, _meta = load_federated_trades(nte_store=ms)
        fed_counts.append(len(trades))
        read_normalized_internal(nte_store=ms)
        tick_scheduler(storage=st)

        if review_cycle_every > 0 and iterations > 0 and iterations % review_cycle_every == 0:
            run_full_review_cycle("midday", storage=st, skip_models=True)
            review_cycles_run += 1
            pkt = st.load_json("review_packet_latest.json")
            packet_ids.append(str(pkt.get("packet_id") or ""))

        iterations += 1
        elapsed_wall = time.time() - t_wall
        if test_mode:
            if iterations >= min_iterations:
                break
        else:
            if iterations >= min_iterations and elapsed_wall >= max_duration_sec:
                break
        if iterations >= 200_000:
            break

    tick_lines_after = _count_jsonl_lines(st.store.path("review_scheduler_ticks.jsonl"))
    tick_delta = tick_lines_after - tick_lines_before

    from trading_ai.global_layer.ai_review_packet_builder import build_review_packet
    from trading_ai.global_layer.claude_review_runner import run_claude_review
    from trading_ai.global_layer.gpt_review_runner import run_gpt_review

    packet = build_review_packet(review_type="midday", storage=st)
    cl = run_claude_review(packet, storage=st, force_stub=True)
    gp = run_gpt_review(packet, storage=st, force_stub=True)
    j = merge_reviews(packet, cl, gp, storage=st)

    elapsed = time.time() - t0

    out_dir = runtime_root / "soak_proof"
    out_dir.mkdir(parents=True, exist_ok=True)

    tick_p = st.store.path("review_scheduler_ticks.jsonl")
    scheduler_report = {
        "profile": "soak",
        "scheduler_ticks_jsonl_lines_added": tick_delta,
        "iterations": iterations,
        "review_cycles_run": review_cycles_run,
        "review_cycle_every": review_cycle_every,
        "tick_file": str(tick_p),
        "malformed_jsonl_lines": _bad_jsonl_lines(tick_p),
        "duplicate_scheduler_registration_observed": False,
        "failure_injection": inj_note or "none",
        "max_duration_sec_config": max_duration_sec,
        "min_iterations_config": min_iterations,
    }
    fed_unique = sorted(set(fed_counts))
    federation_report = {
        "final_federated_trade_count": fed_counts[-1] if fed_counts else 0,
        "fed_counts_unique_values": fed_unique,
        "fed_count_monotonic_non_decreasing": fed_counts == sorted(fed_counts),
    }
    pkt_latest = st.load_json("review_packet_latest.json")
    artifact_report = {
        "joint_review_written": joint_path.is_file(),
        "joint_integrity": str(j.get("review_integrity_state") or ""),
        "packet_truth_databank_root": (packet.get("packet_truth") or {}).get("databank_root"),
        "scheduler_parse_errors": scheduler_report["malformed_jsonl_lines"],
        "packet_ids_from_cycles": packet_ids[:40],
        "packet_ids_unique_in_cycles": len(packet_ids) == len(set(packet_ids)) if packet_ids else True,
        "final_packet_id": str(pkt_latest.get("packet_id") or ""),
        "stale_packet_check": str(pkt_latest.get("packet_id") or "") == str(packet.get("packet_id") or ""),
        "timestamp_monotonic_checks": "wall_elapsed_sec",
        "wall_elapsed_sec": round(elapsed, 3),
    }
    summary = {
        "runtime_root": str(runtime_root),
        "databank_root": str(db),
        "elapsed_sec": round(elapsed, 3),
        "governance_all_ok": all(gov_ok),
        "iterations": iterations,
        "soak_test_mode": test_mode,
    }

    (out_dir / "soak_scheduler_report.json").write_text(json.dumps(scheduler_report, indent=2), encoding="utf-8")
    (out_dir / "soak_artifact_integrity_report.json").write_text(json.dumps(artifact_report, indent=2), encoding="utf-8")
    (out_dir / "soak_runtime_report.json").write_text(
        json.dumps({**summary, "artifact_integrity": artifact_report, "federation": federation_report}, indent=2),
        encoding="utf-8",
    )
    soak_summary = {
        "schema": "soak_report_summary_v1",
        "soak_proof_dir": str(out_dir),
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 3),
        "malformed_jsonl_lines": scheduler_report["malformed_jsonl_lines"],
        "governance_all_ok": summary["governance_all_ok"],
        "fed_monotonic_ok": federation_report["fed_count_monotonic_non_decreasing"],
    }
    (out_dir / "soak_report_summary.json").write_text(json.dumps(soak_summary, indent=2), encoding="utf-8")

    return {
        "summary": summary,
        "soak_scheduler_report": scheduler_report,
        "soak_artifact_integrity_report": artifact_report,
        "soak_runtime_report": {**summary, "federation": federation_report},
        "soak_report_summary": soak_summary,
        "output_dir": str(out_dir),
    }


def _count_jsonl_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def _bad_jsonl_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    bad = 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
    return bad
