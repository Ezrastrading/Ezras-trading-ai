"""
Coinbase Avenue A — shadow/paper runtime proof harness (no live capital).

Produces artifacts under a caller-provided ``EZRAS_RUNTIME_ROOT`` for inspection.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


def _write_joint(gdir: Path, payload: Dict[str, Any]) -> None:
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "joint_review_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _nte_gate_like_engine(product_id: str, route: str) -> Tuple[bool, str, Dict[str, Any]]:
    from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full

    return check_new_order_allowed_full(
        venue="coinbase",
        operation="nte_new_entry",
        route=route,
        intent_id=product_id,
        log_decision=True,
    )


def run_governance_runtime_scenarios(
    runtime_root: Path,
    *,
    log_lines: List[str],
) -> Dict[str, Any]:
    """Enforcement off, paused block, caution block, normal pass — same gate API as NTE."""
    gdir = runtime_root / "shark" / "memory" / "global"
    out: Dict[str, Any] = {"scenarios": []}

    def capture(name: str, setup: Callable[[], None], fn: Callable[[], Tuple[bool, str, Dict[str, Any]]]) -> None:
        setup()
        ok, reason, audit = fn()
        log_lines.append(f"[{name}] ok={ok} reason={reason} audit={json.dumps(audit, default=str)}")
        out["scenarios"].append({"name": name, "ok": ok, "reason": reason, "audit": audit})

    # 1) Advisory (enforcement off)
    def s1() -> None:
        os.environ.pop("GOVERNANCE_ORDER_ENFORCEMENT", None)
        os.environ.pop("GOVERNANCE_CAUTION_BLOCK_ENTRIES", None)
        _write_joint(
            gdir,
            {
                "joint_review_id": "jr_adv",
                "live_mode_recommendation": "paused",
                "review_integrity_state": "full",
                "generated_at": "2099-01-01T12:00:00+00:00",
                "packet_id": "pkt_adv",
                "empty": False,
            },
        )

    capture("enforcement_off_advisory", s1, lambda: _nte_gate_like_engine("BTC-USD", "n/a"))

    # 2) Paused + enforcement on → block
    def s2() -> None:
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        _write_joint(
            gdir,
            {
                "joint_review_id": "jr_p",
                "live_mode_recommendation": "paused",
                "review_integrity_state": "full",
                "generated_at": "2099-01-01T12:00:00+00:00",
                "packet_id": "pkt_p",
                "empty": False,
            },
        )

    capture("enforcement_on_paused_blocks", s2, lambda: _nte_gate_like_engine("BTC-USD", "n/a"))

    # 3) Caution + block flag
    def s3() -> None:
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        os.environ["GOVERNANCE_CAUTION_BLOCK_ENTRIES"] = "true"
        _write_joint(
            gdir,
            {
                "joint_review_id": "jr_c",
                "live_mode_recommendation": "caution",
                "review_integrity_state": "full",
                "generated_at": "2099-01-01T12:00:00+00:00",
                "packet_id": "pkt_c",
                "empty": False,
            },
        )

    capture("enforcement_on_caution_blocks", s3, lambda: _nte_gate_like_engine("ETH-USD", "n/a"))

    # 4) Normal pass
    def s4() -> None:
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        os.environ.pop("GOVERNANCE_CAUTION_BLOCK_ENTRIES", None)
        _write_joint(
            gdir,
            {
                "joint_review_id": "jr_n",
                "live_mode_recommendation": "normal",
                "review_integrity_state": "full",
                "generated_at": "2099-01-01T12:00:00+00:00",
                "packet_id": "pkt_n",
                "empty": False,
            },
        )

    capture("enforcement_on_normal_pass", s4, lambda: _nte_gate_like_engine("SOL-USD", "n/a"))

    return out


def run_close_chain(
    runtime_root: Path,
    databank_root: Path,
    *,
    trade_id: str = "cb_rt_proof_001",
    exit_reason: str = "take_profit",
    hard_stop: bool = False,
    product_id: str = "BTC-USD",
    skip_entry_gate: bool = False,
) -> Dict[str, Any]:
    """
    Paper close: MemoryStore trade → adapter → process_closed_trade → federated read → packet (stub review).

    Use ``hard_stop=True`` for stop-loss / hard-stop adapter path (``exit_reason=stop_loss``, anomaly flags).
    """
    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(databank_root)

    # Same governance API as NTE ``_maybe_enter`` (entry intent proof before close pipeline).
    if skip_entry_gate:
        gate_ok, gate_reason, gate_audit = True, "skipped_session_already_gated", {}
    else:
        gate_ok, gate_reason, gate_audit = _nte_gate_like_engine(product_id, "n/a")

    from trading_ai.global_layer.internal_data_reader import read_normalized_internal
    from trading_ai.global_layer.review_scheduler import run_full_review_cycle
    from trading_ai.global_layer.review_storage import ReviewStorage
    from trading_ai.global_layer.trade_truth import load_federated_trades
    from trading_ai.nte.databank.coinbase_close_adapter import coinbase_nt_close_to_databank_raw
    from trading_ai.nte.databank.trade_intelligence_databank import process_closed_trade
    from trading_ai.nte.memory.store import MemoryStore

    ms = MemoryStore()
    ms.ensure_defaults()

    opened = time.time() - 3600.0
    er = "stop_loss" if hard_stop else exit_reason
    pos = {
        "id": trade_id,
        "product_id": product_id,
        "strategy": "mean_reversion",
        "opened_ts": opened,
        "entry_regime": "calm",
    }
    record = {
        "trade_id": trade_id,
        "product_id": product_id,
        "setup_type": "mean_reversion",
        "duration_sec": 120.0,
        "gross_pnl_usd": -3.20 if hard_stop else 2.50,
        "net_pnl_usd": -3.50 if hard_stop else 2.10,
        "fees_usd": 0.30 if hard_stop else 0.40,
        "realized_move_bps": 8.0,
        "expected_edge_bps": 12.0,
        "router_score_a": 0.72,
        "router_score_b": 0.41,
        "execution_type": "market",
        "regime": "calm",
    }
    if hard_stop:
        record["mistake_classification"] = "hit_stop"
    raw_db = coinbase_nt_close_to_databank_raw(pos, record, exit_reason=er)

    mem_row = {
        "trade_id": trade_id,
        "avenue": "coinbase",
        "avenue_name": "coinbase",
        "route_bucket": "synthetic_default",
        "strategy_class": "mean_reversion",
        "setup_type": "mean_reversion",
        "product_id": product_id,
        "net_pnl_usd": record["net_pnl_usd"],
        "gross_pnl_usd": record["gross_pnl_usd"],
        "fees_usd": record["fees_usd"],
        "entry_slippage_bps": 2.0,
        "exit_slippage_bps": 2.0,
        "exit_reason": er,
        "hard_stop_exit": bool(hard_stop),
        "timestamp_open": raw_db["timestamp_open"],
        "timestamp_close": raw_db["timestamp_close"],
    }
    if hard_stop:
        mem_row["anomaly_flags"] = ["hard_stop_exit"]
    ms.append_trade(mem_row)

    proc = process_closed_trade(raw_db)

    fed_trades, fed_meta = load_federated_trades(nte_store=ms)
    internal = read_normalized_internal(nte_store=ms)

    st = ReviewStorage()
    st.ensure_review_files()
    cycle = run_full_review_cycle("midday", storage=st, skip_models=True)

    tm_path = ms.path("trade_memory.json")
    te_path = databank_root / "trade_events.jsonl"
    rp_path = st.store.path("review_packet_latest.json")
    jr_path = st.store.path("joint_review_latest.json")
    tick_path = st.store.path("review_scheduler_ticks.jsonl")

    from trading_ai.global_layer.review_scheduler import tick_scheduler

    tick_scheduler(storage=st)

    merged_row = next((t for t in fed_trades if str(t.get("trade_id")) == trade_id), None)
    pkt = cycle.get("packet") or {}
    lt = pkt.get("live_trading_summary") or {}
    rs = pkt.get("risk_summary") or {}

    return {
        "nte_entry_gate": {"ok": gate_ok, "reason": gate_reason, "audit": gate_audit},
        "exit_reason": er,
        "hard_stop": bool(hard_stop),
        "process_closed_trade": proc,
        "federated_trade_ids": [str(t.get("trade_id")) for t in fed_trades],
        "merged_trade": merged_row,
        "packet_hard_stop_events": int(lt.get("hard_stop_events") or 0),
        "risk_hard_stop_events": int(rs.get("hard_stop_events") or 0),
        "trade_truth_meta": fed_meta,
        "internal_trade_count": len(internal.get("trades") or []),
        "review_cycle_keys": list(cycle.keys()),
        "artifact_paths": {
            "trade_memory": str(tm_path),
            "trade_events_jsonl": str(te_path),
            "review_packet_latest": str(rp_path),
            "joint_review_latest": str(jr_path),
            "review_scheduler_ticks": str(tick_path),
        },
    }


def run_scheduler_probe(
    runtime_root: Path,
    databank_root: Path,
    *,
    log_lines: List[str],
) -> Dict[str, Any]:
    """Short APScheduler session: real IntervalTrigger + ai_review_tick id, max_instances=1, coalesce=True."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        return {"ok": False, "error": "apscheduler not installed"}

    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(databank_root)

    from trading_ai.global_layer.review_scheduler import tick_scheduler
    from trading_ai.global_layer.review_storage import ReviewStorage

    st = ReviewStorage()
    st.ensure_review_files()

    invocations: List[float] = []
    lock = threading.Lock()

    def job() -> None:
        with lock:
            invocations.append(time.time())
        tick_scheduler(storage=st)

    sched = BackgroundScheduler(timezone="UTC")
    # Same kwargs as ``shark/scheduler.py`` for ``ai_review_tick`` (IntervalTrigger uses minutes there).
    sched.add_job(
        job,
        IntervalTrigger(seconds=1),
        id="ai_review_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    jobs_pre_start = list(sched.get_jobs())
    sched.start()
    jobs_running = list(sched.get_jobs())
    # Simulate config reload / hot re-register — must not duplicate job id
    sched.add_job(
        job,
        IntervalTrigger(seconds=1),
        id="ai_review_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    jobs_after_replace = list(sched.get_jobs())
    time.sleep(4.2)
    sched.shutdown(wait=False)

    tick_p = st.store.path("review_scheduler_ticks.jsonl")
    n_lines = 0
    if tick_p.is_file():
        n_lines = len([x for x in tick_p.read_text(encoding="utf-8").splitlines() if x.strip()])

    j0 = jobs_after_replace[0] if jobs_after_replace else None
    log_lines.append(
        f"scheduler_pre_start_jobs={len(jobs_pre_start)} running_jobs={len(jobs_running)} "
        f"after_hot_replace={len(jobs_after_replace)} tick_invocations={len(invocations)} jsonl_lines={n_lines}"
    )

    return {
        "ok": True,
        "job_count_pre_start": len(jobs_pre_start),
        "job_count_while_running": len(jobs_running),
        "job_count_after_replace_existing": len(jobs_after_replace),
        "single_ai_review_tick_id": len(jobs_after_replace) == 1 and (j0 and j0.id == "ai_review_tick"),
        "job_id": j0.id if j0 else None,
        "max_instances": getattr(j0, "max_instances", None),
        "coalesce": getattr(j0, "coalesce", None),
        "tick_invocation_count": len(invocations),
        "review_scheduler_ticks_line_count": n_lines,
    }


def run_full_proof(
    runtime_root: Path,
    *,
    databank_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all phases; returns structured report."""
    runtime_root = runtime_root.resolve()
    databank_root = databank_root or (runtime_root / "databank")
    databank_root.mkdir(parents=True, exist_ok=True)

    os.environ["EZRAS_RUNTIME_ROOT"] = str(runtime_root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(databank_root)

    gov_log = runtime_root / "governance_gate_decisions.log"
    gov_logger = logging.getLogger("trading_ai.global_layer.governance_order_gate")
    fh: Optional[logging.FileHandler] = None
    try:
        fh = logging.FileHandler(gov_log, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(message)s"))
        gov_logger.addHandler(fh)

        log_lines: List[str] = []

        gov = run_governance_runtime_scenarios(runtime_root, log_lines=log_lines)

        # Reset joint to normal for close chain + packet
        gdir = runtime_root / "shark" / "memory" / "global"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "joint_review_latest.json").write_text(
            json.dumps(
                {
                    "joint_review_id": "jr_proof_run",
                    "live_mode_recommendation": "normal",
                    "review_integrity_state": "full",
                    "generated_at": "2099-06-15T12:00:00+00:00",
                    "packet_id": "pkt_proof",
                    "empty": False,
                }
            ),
            encoding="utf-8",
        )
        os.environ["GOVERNANCE_ORDER_ENFORCEMENT"] = "true"
        os.environ.pop("GOVERNANCE_CAUTION_BLOCK_ENTRIES", None)

        close_out = run_close_chain(runtime_root, databank_root)
        sched_out = run_scheduler_probe(runtime_root, databank_root, log_lines=log_lines)

        report: Dict[str, Any] = {
            "runtime_root": str(runtime_root),
            "databank_root": str(databank_root),
            "governance": gov,
            "close_chain": close_out,
            "scheduler": sched_out,
            "log_lines": log_lines,
            "artifact_paths_extra": {"governance_gate_log": str(gov_log)},
        }
        out_json = runtime_root / "RUNTIME_PROOF_REPORT.json"
        out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        out_md = runtime_root / "RUNTIME_PROOF_REPORT.md"
        merged = close_out.get("merged_trade") or {}
        out_md.write_text(
            _render_md(report, merged),
            encoding="utf-8",
        )
        return report
    finally:
        if fh is not None:
            try:
                gov_logger.removeHandler(fh)
                fh.close()
            except Exception:
                pass


def _render_md(report: Dict[str, Any], merged: Dict[str, Any]) -> str:
    proc = (report.get("close_chain") or {}).get("process_closed_trade") or {}
    lines = [
        "# Coinbase Avenue A — Shadow/Paper Runtime Proof",
        "",
        "Local databank stages (validate, JSONL, summaries) run; **Supabase upsert is absent in most dev "
        "environments**, so `process_closed_trade.ok` may be false while local `trade_events.jsonl` is still appended.",
        "",
        "## process_closed_trade summary",
        "",
        "```json",
        json.dumps({"ok": proc.get("ok"), "trade_id": proc.get("trade_id"), "stages": proc.get("stages")}, indent=2),
        "```",
        "",
        "## End-to-end merged trade (federated)",
        "",
        "```json",
        json.dumps(merged, indent=2, default=str),
        "```",
        "",
        "## Scheduler probe",
        "",
        "```json",
        json.dumps(report.get("scheduler"), indent=2, default=str),
        "```",
        "",
        "## Governance scenarios",
        "",
        "```json",
        json.dumps(report.get("governance"), indent=2, default=str),
        "```",
        "",
        "## Log lines",
        "",
        "\n".join(f"- {x}" for x in report.get("log_lines") or []),
        "",
    ]
    return "\n".join(lines)
