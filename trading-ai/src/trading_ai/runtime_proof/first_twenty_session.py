"""
Coinbase Avenue A — first-20 shadow/paper verification session (no real capital).

Default: **simulated** completed trades through the same organism path as production paper
(governance → memory → databank → federation → packet/joint).

Operator live hook is **not** wired here; use this harness for verification and artifact discipline.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_proof.agnostic_verification import evaluate_organism_agnostic_lock, final_readiness_flags

LOG = logging.getLogger(__name__)

MAX_TRADES_DEFAULT = 20
MIN_NOTIONAL_USD = 10.0  # informational; NTE paper uses engine minimums


@dataclass
class RollbackThresholds:
    """Explicit rollback thresholds (printed before session start)."""

    max_consecutive_gate_anomalies: int = 5
    """Count consecutive entry attempts where enforcement is on and gate returned allowed=False unexpectedly — see harness logic."""

    max_process_closed_local_failures: int = 3
    """Rollback if ``local_raw_event`` is False in ``process_closed_trade`` stages that many times."""

    max_federation_conflict_spike: int = 15
    """Rollback if ``federation_conflict_count`` in trade_truth_meta exceeds this."""

    max_scheduler_tick_parse_errors: int = 0
    """Any malformed JSONL line in review_scheduler_ticks.jsonl triggers rollback."""

    joint_review_paused_stops: bool = True
    """Stop immediately if ``joint_review_latest`` live_mode is paused (when readable)."""


@dataclass
class FirstTwentySessionConfig:
    runtime_root: Path
    databank_root: Optional[Path] = None
    artifact_archive: Optional[Path] = None
    session_id: str = field(default_factory=lambda: f"ft20_{uuid.uuid4().hex[:12]}")
    paper_shadow_mode: str = "paper"
    max_completed_trades: int = MAX_TRADES_DEFAULT
    rollback: RollbackThresholds = field(default_factory=RollbackThresholds)
    smallest_notional_usd: float = MIN_NOTIONAL_USD
    # Simulation: vary products and outcomes
    products: Tuple[str, ...] = ("BTC-USD", "ETH-USD", "SOL-USD")


def _doc_local_first_path() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "COINBASE_AVENUE_A_PRELIVE_DATA_STANCE.md"


def run_preflight(cfg: FirstTwentySessionConfig) -> Tuple[Dict[str, Any], List[Tuple[str, bool, str]]]:
    """
    Returns (manifest_dict, checklist list of (name, ok, detail)).

    Aborts caller responsibility: if any ok is False, do not start session.
    """
    root = cfg.runtime_root.resolve()
    db = (cfg.databank_root or (root / "databank")).resolve()
    arch = (cfg.artifact_archive or (root / "first_20_sessions" / cfg.session_id)).resolve()

    checklist: List[Tuple[str, bool, str]] = []

    # 1 Runtime root writable
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".first20_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checklist.append(("runtime_root_writable", True, str(root)))
    except OSError as e:
        checklist.append(("runtime_root_writable", False, str(e)))

    # 2 Governance env explicit (at least logged)
    gov_enf = (os.environ.get("GOVERNANCE_ORDER_ENFORCEMENT") or "").strip().lower()
    checklist.append(
        (
            "governance_env_documented",
            True,
            f"GOVERNANCE_ORDER_ENFORCEMENT={gov_enf or '(unset=advisory)'}",
        )
    )

    # 3 Coinbase / NTE paper-safe
    nte_mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    paper = (os.environ.get("NTE_PAPER_MODE") or "").strip().lower() in ("1", "true", "yes")
    live_cb = (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() in ("1", "true", "yes")
    safe = (nte_mode == "paper" or paper or not live_cb) and (os.environ.get("FIRST_TWENTY_ALLOW_LIVE", "").lower() not in ("1", "true", "yes"))
    checklist.append(
        (
            "coinbase_paper_shadow_safe",
            safe,
            f"NTE_EXECUTION_MODE={nte_mode} NTE_PAPER_MODE={paper} COINBASE_ENABLED={live_cb} "
            "(set FIRST_TWENTY_ALLOW_LIVE=1 only for supervised non-paper — not default)",
        )
    )

    # 4 Artifact archive
    try:
        arch.mkdir(parents=True, exist_ok=True)
        checklist.append(("artifact_archive_ready", True, str(arch)))
    except OSError as e:
        checklist.append(("artifact_archive_ready", False, str(e)))

    # 5 Review scheduler — intentional
    tick_en = (os.environ.get("AI_REVIEW_TICK_ENABLED") or "true").strip().lower()
    checklist.append(
        (
            "review_scheduler_note",
            True,
            f"AI_REVIEW_TICK_ENABLED={tick_en} (harness calls tick_scheduler per close)",
        )
    )

    # 6 Local-first doc
    doc = _doc_local_first_path()
    checklist.append(("local_first_v1_documented", doc.is_file(), str(doc)))

    # 7 Rollback thresholds
    checklist.append(("rollback_thresholds_loaded", True, json.dumps(asdict(cfg.rollback), default=str)))

    # 8 Session id
    checklist.append(("session_id_created", bool(cfg.session_id), cfg.session_id))

    manifest = {
        "session_id": cfg.session_id,
        "start_time": time.time(),
        "start_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "databank_root": str(db),
        "artifact_archive": str(arch),
        "paper_or_shadow_mode": cfg.paper_shadow_mode,
        "smallest_notional_usd": cfg.smallest_notional_usd,
        "max_completed_trades": cfg.max_completed_trades,
        "governance": {
            "GOVERNANCE_ORDER_ENFORCEMENT": os.environ.get("GOVERNANCE_ORDER_ENFORCEMENT"),
            "GOVERNANCE_CAUTION_BLOCK_ENTRIES": os.environ.get("GOVERNANCE_CAUTION_BLOCK_ENTRIES"),
        },
        "nte": {
            "NTE_EXECUTION_MODE": os.environ.get("NTE_EXECUTION_MODE"),
            "NTE_PAPER_MODE": os.environ.get("NTE_PAPER_MODE"),
            "COINBASE_ENABLED": os.environ.get("COINBASE_ENABLED"),
        },
        "rollback_thresholds": asdict(cfg.rollback),
        "artifact_paths_expected": {
            "trade_memory": str(root / "shark" / "nte" / "memory" / "trade_memory.json"),
            "trade_events_jsonl": str(db / "trade_events.jsonl"),
            "review_packet_latest": str(root / "shark" / "memory" / "global" / "review_packet_latest.json"),
            "joint_review_latest": str(root / "shark" / "memory" / "global" / "joint_review_latest.json"),
            "review_scheduler_ticks": str(root / "shark" / "memory" / "global" / "review_scheduler_ticks.jsonl"),
            "governance_gate_log": str(root / "governance_gate_decisions.log"),
        },
    }
    man_path = arch / "session_manifest.json"
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["session_manifest_path"] = str(man_path)

    return manifest, checklist


def _read_joint_mode(runtime_root: Path) -> Optional[str]:
    p = runtime_root / "shark" / "memory" / "global" / "joint_review_latest.json"
    if not p.is_file():
        return None
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        return str(j.get("live_mode_recommendation") or "").strip().lower()
    except Exception:
        return None


def _validate_scheduler_ticks_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    errs = 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            json.loads(ln)
        except json.JSONDecodeError:
            errs += 1
    return errs


def _append_governance_file(runtime_root: Path, record: Dict[str, Any]) -> None:
    p = runtime_root / "first_20_governance_attempts.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def run_first_twenty_shadow_session(
    cfg: FirstTwentySessionConfig,
    *,
    simulate_trades: int = 20,
) -> Dict[str, Any]:
    """
    Run up to ``simulate_trades`` (cap ``max_completed_trades``) synthetic closes.

    Each iteration: pre-rollback checks → governance (NTE-style) → ``run_close_chain`` for one trade.
    """
    from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
    from trading_ai.runtime_proof.coinbase_shadow_paper_pass import run_close_chain

    root = cfg.runtime_root.resolve()
    db = (cfg.databank_root or (root / "databank")).resolve()
    arch = (cfg.artifact_archive or (root / "first_20_sessions" / cfg.session_id)).resolve()
    arch.mkdir(parents=True, exist_ok=True)

    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    os.environ["TRADE_DATABANK_MEMORY_ROOT"] = str(db)
    if cfg.paper_shadow_mode == "paper":
        os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
        os.environ.setdefault("NTE_PAPER_MODE", "true")

    manifest, checklist = run_preflight(cfg)
    critical = {"runtime_root_writable", "artifact_archive_ready", "coinbase_paper_shadow_safe"}
    preflight_ok = all(ok for name, ok, _ in checklist if name in critical)
    if not preflight_ok:
        bad = [(a, d) for a, ok, d in checklist if not ok]
        return {
            "status": "aborted_preflight",
            "checklist": [{"name": a, "ok": o, "detail": d} for a, o, d in checklist],
            "failures": [{"name": a, "detail": d} for a, d in bad],
        }

    LOG.warning(
        "First-20 rollback thresholds: %s",
        json.dumps(asdict(cfg.rollback), default=str),
    )

    n = min(int(simulate_trades), cfg.max_completed_trades)
    trades_out: List[Dict[str, Any]] = []
    cumulative = {
        "completed": 0,
        "wins": 0,
        "losses": 0,
        "hard_stop_count": 0,
        "federation_conflict_total": 0,
        "missing_field_count": 0,
        "scheduler_anomalies": 0,
        "gate_anomalies": 0,
        "process_closed_local_failures": 0,
    }
    consecutive_gate_issues = 0
    rollback_reason: Optional[str] = None

    # Ensure joint not paused for simulation (operator must set real joint file)
    joint_path = root / "shark" / "memory" / "global" / "joint_review_latest.json"
    joint_path.parent.mkdir(parents=True, exist_ok=True)
    if not joint_path.is_file():
        joint_path.write_text(
            json.dumps(
                {
                    "joint_review_id": f"jr_{cfg.session_id}",
                    "live_mode_recommendation": "normal",
                    "review_integrity_state": "full",
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "packet_id": "pkt_ft20_seed",
                    "empty": False,
                }
            ),
            encoding="utf-8",
        )

    products = list(cfg.products)

    for i in range(1, n + 1):
        if rollback_reason:
            break

        mode = _read_joint_mode(root)
        if cfg.rollback.joint_review_paused_stops and mode == "paused":
            rollback_reason = "joint_review_paused"
            break

        pid = products[(i - 1) % len(products)]
        tid = f"{cfg.session_id}_t{i:02d}"
        hard = i % 7 == 0  # periodic synthetic hard stop

        ok_g, reason_g, audit_g = check_new_order_allowed_full(
            venue="coinbase",
            operation="first20_entry",
            route="n/a",
            intent_id=f"{pid}_{i}",
            log_decision=True,
        )
        _append_governance_file(
            root,
            {
                "trade_number": i,
                "trade_id": tid,
                "attempt": "entry",
                "allowed": ok_g,
                "reason": reason_g,
                "audit": audit_g,
                "ts": time.time(),
            },
        )

        enf = (audit_g.get("enforcement_enabled") is True)
        if enf and not ok_g:
            consecutive_gate_issues += 1
            cumulative["gate_anomalies"] += 1
            if consecutive_gate_issues >= cfg.rollback.max_consecutive_gate_anomalies:
                rollback_reason = "consecutive_gate_block"
                break
        else:
            consecutive_gate_issues = 0

        if not ok_g:
            trades_out.append(
                {
                    "trade_number": i,
                    "trade_id": tid,
                    "status": "entry_blocked",
                    "governance_reason": reason_g,
                }
            )
            _write_session_report(root, arch, cfg, trades_out, cumulative, rollback_reason, manifest)
            continue

        close = run_close_chain(
            root,
            db,
            trade_id=tid,
            hard_stop=hard,
            exit_reason="stop_loss" if hard else "take_profit",
            product_id=pid,
            skip_entry_gate=True,
        )

        proc = close.get("process_closed_trade") or {}
        stages = proc.get("stages") or {}
        if stages.get("local_raw_event") is False:
            cumulative["process_closed_local_failures"] += 1
            if cumulative["process_closed_local_failures"] >= cfg.rollback.max_process_closed_local_failures:
                rollback_reason = "process_closed_local_failure"
                break

        merged = close.get("merged_trade") or {}
        meta = close.get("trade_truth_meta") or {}
        fc = int(meta.get("federation_conflict_count") or 0)
        cumulative["federation_conflict_total"] = max(cumulative["federation_conflict_total"], fc)
        if fc >= cfg.rollback.max_federation_conflict_spike:
            rollback_reason = "federation_conflict_spike"
            break

        tick_p = Path(close["artifact_paths"]["review_scheduler_ticks"])
        parse_errs = _validate_scheduler_ticks_jsonl(tick_p)
        cumulative["scheduler_anomalies"] = parse_errs
        if parse_errs > cfg.rollback.max_scheduler_tick_parse_errors:
            rollback_reason = "scheduler_tick_malformed"
            break

        net = merged.get("net_pnl_usd")
        try:
            nf = float(net) if net is not None else None
        except (TypeError, ValueError):
            nf = None
        if nf is not None:
            if nf > 0:
                cumulative["wins"] += 1
            elif nf < 0:
                cumulative["losses"] += 1
        if hard:
            cumulative["hard_stop_count"] += 1

        # Missing "critical" fields for shadow review
        missing = 0
        for k in ("trade_id", "avenue", "net_pnl_usd"):
            if merged.get(k) is None and k != "net_pnl_usd":
                missing += 1
        if merged.get("net_pnl_usd") is None:
            missing += 1
        cumulative["missing_field_count"] += missing

        pkt_ok = bool(merged.get("trade_id"))
        trades_out.append(
            {
                "trade_number": i,
                "trade_id": tid,
                "entry_timestamp": merged.get("timestamp_open") or merged.get("logged_at"),
                "close_timestamp": merged.get("timestamp_close"),
                "product": pid,
                "route_setup": merged.get("setup_type"),
                "gross_pnl_usd": merged.get("gross_pnl_usd"),
                "net_pnl_usd": merged.get("net_pnl_usd"),
                "fees_usd": merged.get("fees_usd"),
                "slippage_entry_bps": merged.get("entry_slippage_bps"),
                "slippage_exit_bps": merged.get("exit_slippage_bps"),
                "hard_stop_exit": bool(merged.get("hard_stop_exit")),
                "exit_reason": close.get("exit_reason"),
                "governance_decision_reason": reason_g,
                "federation_included": tid in (close.get("federated_trade_ids") or []),
                "packet_inclusion_confirmed": pkt_ok,
                "process_closed_trade_ok": bool(proc.get("ok")),
                "federation_conflict_count": fc,
            }
        )
        cumulative["completed"] += 1

        # Snapshot archive
        snap = arch / "per_trade" / f"trade_{i:02d}.json"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(trades_out[-1], indent=2, default=str), encoding="utf-8")

        _write_session_report(root, arch, cfg, trades_out, cumulative, rollback_reason, manifest)

    # Final MD summary
    verdict = _verdict_from_cumulative(cumulative, rollback_reason, n, len(trades_out))
    agnostic_ev = evaluate_organism_agnostic_lock(runtime_root=root, run_tests=True)
    readiness = final_readiness_flags(agnostic_ev)
    final = {
        "status": "completed" if not rollback_reason else "aborted_rollback",
        "rollback_reason": rollback_reason,
        "session_id": cfg.session_id,
        "manifest": manifest,
        "checklist": [{"name": a, "ok": o, "detail": d} for a, o, d in checklist],
        "trades": trades_out,
        "cumulative": cumulative,
        "recommendation": verdict,
        "organism_agnostic_lock": agnostic_ev,
        "readiness_flags": readiness,
        "organism_status": _organism_status_verdict(agnostic_ev, readiness),
    }
    _write_session_report(root, arch, cfg, trades_out, cumulative, rollback_reason, manifest, final_verdict=verdict)
    (arch / "first_20_session_report.final.json").write_text(json.dumps(final, indent=2, default=str), encoding="utf-8")
    _write_session_md(arch, final)

    # Copy key artifacts into archive
    try:
        for label, src in [
            ("trade_memory.json", root / "shark" / "nte" / "memory" / "trade_memory.json"),
            ("trade_events.jsonl", db / "trade_events.jsonl"),
        ]:
            if src.is_file():
                shutil.copy2(src, arch / f"bundle_{label}")
    except OSError as exc:
        LOG.warning("artifact bundle copy: %s", exc)

    return final


def _organism_status_verdict(agnostic_ev: Dict[str, Any], readiness: Dict[str, Any]) -> str:
    """FULLY_AGNOSTIC only when tests + packet checks pass and no dependency flags."""
    if any(
        readiness.get(k)
        for k in ("strategy_dependency_detected", "latency_dependency_detected", "edge_dependency_detected")
    ):
        return "FAILED"
    if readiness.get("organism_agnostic") and agnostic_ev.get("agnosticity_verified"):
        return "FULLY_AGNOSTIC"
    if agnostic_ev.get("agnosticity_verified") or agnostic_ev.get("agnostic_unit_tests_passed"):
        return "PARTIAL"
    return "FAILED"


def _verdict_from_cumulative(
    cumulative: Dict[str, Any],
    rollback: Optional[str],
    planned: int,
    recorded: int,
) -> str:
    if rollback:
        return "FAIL_DO_NOT_PROCEED"
    if cumulative["scheduler_anomalies"] > 0:
        return "PARTIAL_PASS_NEEDS_FIXES"
    if cumulative["completed"] < planned:
        return "PARTIAL_PASS_NEEDS_FIXES"
    if cumulative["process_closed_local_failures"] > 0:
        return "PARTIAL_PASS_NEEDS_FIXES"
    return "PASS_SHADOW_VERIFICATION"


def _write_session_report(
    root: Path,
    arch: Path,
    cfg: FirstTwentySessionConfig,
    trades: List[Dict[str, Any]],
    cumulative: Dict[str, Any],
    rollback: Optional[str],
    manifest: Dict[str, Any],
    final_verdict: Optional[str] = None,
) -> None:
    rep = {
        "session_id": cfg.session_id,
        "updated_at": time.time(),
        "rollback_active": rollback,
        "cumulative": cumulative,
        "trades": trades,
        "manifest_runtime_root": manifest.get("runtime_root"),
    }
    if final_verdict:
        rep["recommendation"] = final_verdict
    p = root / "first_20_session_report.json"
    arch_p = arch / "first_20_session_report.json"
    js = json.dumps(rep, indent=2, default=str)
    p.write_text(js, encoding="utf-8")
    arch_p.write_text(js, encoding="utf-8")


def _write_session_md(arch: Path, final: Dict[str, Any]) -> None:
    lines = [
        "# First-20 shadow session report",
        "",
        f"**session_id:** `{final.get('session_id')}`",
        f"**status:** {final.get('status')}",
        f"**rollback:** {final.get('rollback_reason')}",
        f"**recommendation:** {final.get('recommendation')}",
        "",
        "## Cumulative",
        "",
        "```json",
        json.dumps(final.get("cumulative"), indent=2),
        "```",
        "",
    ]
    (arch / "first_20_session_report.md").write_text("\n".join(lines), encoding="utf-8")


