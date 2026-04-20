"""
Verify command center, diagnosis, CEO review, and trading memory artifacts after ops runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.control.paths import command_center_report_path, command_center_snapshot_path
from trading_ai.deployment.paths import checklist_json_path, ops_outputs_proof_path
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.learning.paths import trading_memory_path
from trading_ai.nte.databank.local_trade_store import global_trade_events_path, path_daily_summary, path_weekly_summary
from trading_ai.reality.paths import trade_logs_dir
from trading_ai.review.paths import ceo_daily_review_json_path, ceo_daily_review_txt_path, daily_diagnosis_path
from trading_ai.runtime_paths import ezras_runtime_root


def _mtime(p: Path) -> Optional[float]:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def run_ops_outputs_bundle(
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run command center snapshot, daily diagnosis, CEO review; touch trading memory timestamp.

    Returns a compact result dict (used after live validation rounds).
    """
    from trading_ai.control.command_center import run_command_center_snapshot
    from trading_ai.learning.trading_memory import load_trading_memory, save_trading_memory
    from trading_ai.review.daily_diagnosis import run_daily_diagnosis
    from trading_ai.review.ceo_review_session import run_ceo_review_session

    _ = runtime_root
    run_command_center_snapshot(write_files=True)
    run_daily_diagnosis(write_files=True)
    run_ceo_review_session()

    mem = load_trading_memory()
    lessons = list(mem.get("execution_lessons") or [])
    lessons.insert(0, f"deployment_ops_bundle:{iso_now()}")
    mem["execution_lessons"] = lessons[:80]
    save_trading_memory(mem)

    return verify_ops_outputs_proof(write_file=True)


def verify_ops_outputs_proof(*, write_file: bool = True) -> Dict[str, Any]:
    """
    Confirm expected files exist and are non-empty; writes ``ops_outputs_proof.json``.

    Includes databank trade log + rollups (when present after closed-trade processing),
    command center, diagnosis, CEO, memory, deployment checklist artifact, execution proof.
    """
    rt = ezras_runtime_root()
    exec_proof = rt / "execution_proof" / "live_execution_validation.json"
    paths = {
        "command_center_snapshot.json": command_center_snapshot_path(),
        "command_center_report.txt": command_center_report_path(),
        "daily_diagnosis.json": daily_diagnosis_path(),
        "ceo_daily_review.json": ceo_daily_review_json_path(),
        "ceo_daily_review.txt": ceo_daily_review_txt_path(),
        "trading_memory.json": trading_memory_path(),
        "trade_events.jsonl": global_trade_events_path(),
        "daily_trade_summary.json": path_daily_summary(),
        "weekly_trade_summary.json": path_weekly_summary(),
        "deployment_checklist.json": checklist_json_path(),
        "execution_proof_live_validation.json": exec_proof,
    }
    checks: Dict[str, Any] = {}
    all_ok = True
    for label, p in paths.items():
        sz = p.stat().st_size if p.is_file() else 0
        ok = p.is_file() and sz > 2
        checks[label] = {"path": str(p), "ok": ok, "size": sz, "mtime": _mtime(p)}
        if not ok:
            all_ok = False

    tdir = trade_logs_dir()
    tl_ok = tdir.is_dir() and any(tdir.glob("*.jsonl"))
    checks["trade_logs_jsonl_any"] = {"path": str(tdir), "ok": tl_ok, "advisory_only": True}

    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "ops_outputs_ok": all_ok,
        "checks": checks,
    }
    if write_file:
        ops_outputs_proof_path().parent.mkdir(parents=True, exist_ok=True)
        ops_outputs_proof_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
