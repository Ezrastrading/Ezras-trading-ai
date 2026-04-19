"""
Final pre-live blocker closure — Coinbase Avenue A (orchestration + rubric).

Does **not** place live capital. Produces ``BLOCKER_CLOSURE_REPORT.json`` under the runtime root.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


def coinbase_public_connectivity_probe(product_id: str = "BTC-USD") -> Dict[str, Any]:
    """Read-only public Coinbase spot (no API keys). Proves outbound HTTPS + Coinbase price surface."""
    try:
        from trading_ai.shark.outlets.coinbase import _public_spot

        px = _public_spot(product_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "product_id": product_id}
    return {
        "ok": px is not None and px > 0,
        "spot_usd": px,
        "product_id": product_id,
        "note": "Public read-only; not an authenticated order or close.",
    }


def run_scheduler_tick_stress(
    runtime_root: Path,
    *,
    n_ticks: int = 600,
    isolated: bool = True,
) -> Dict[str, Any]:
    """
    Accelerated long-run equivalent: many ``tick_scheduler`` invocations with gates disabled.

    Proves JSONL coherence (parse every line), evaluate/complete pairing.
    With ``isolated=True`` (default), uses ``<runtime_root>/tick_stress_isolated`` as
    ``EZRAS_RUNTIME_ROOT`` so counts are not mixed with prior tick JSONL lines.

    **Does not** replace a multi-hour ``run_shark`` staging run (see ``staging_hours_note``).
    """
    stress_root = (runtime_root / "tick_stress_isolated") if isolated else runtime_root
    stress_root.mkdir(parents=True, exist_ok=True)
    saved_root = os.environ.get("EZRAS_RUNTIME_ROOT")
    os.environ["EZRAS_RUNTIME_ROOT"] = str(stress_root.resolve())

    from unittest.mock import patch

    from trading_ai.global_layer.review_scheduler import tick_scheduler
    from trading_ai.global_layer.review_storage import ReviewStorage

    try:
        st = ReviewStorage()
        st.ensure_review_files()
        tick_p = st.store.path("review_scheduler_ticks.jsonl")
        size_before = tick_p.stat().st_size if tick_p.is_file() else 0

        with patch("trading_ai.global_layer.review_scheduler.should_run_morning", lambda *a, **k: False):
            with patch("trading_ai.global_layer.review_scheduler.should_run_midday", lambda *a, **k: False):
                with patch("trading_ai.global_layer.review_scheduler.should_run_eod", lambda *a, **k: False):
                    for _ in range(n_ticks):
                        tick_scheduler(storage=st)

        if not tick_p.is_file():
            return {"ok": False, "error": "review_scheduler_ticks.jsonl missing"}

        raw = tick_p.read_text(encoding="utf-8")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        phases: List[str] = []
        parse_errors = 0
        for ln in lines:
            try:
                rec = json.loads(ln)
                phases.append(str(rec.get("phase") or ""))
            except json.JSONDecodeError:
                parse_errors += 1

        ev = phases.count("tick_evaluate")
        co = phases.count("tick_complete")
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

        return {
            "ok": parse_errors == 0 and ev == co == n_ticks,
            "n_ticks": n_ticks,
            "isolated_runtime_root": str(stress_root.resolve()),
            "line_count": len(lines),
            "tick_evaluate_count": ev,
            "tick_complete_count": co,
            "parse_errors": parse_errors,
            "jsonl_sha256_prefix": h,
            "size_before": size_before,
            "size_after": tick_p.stat().st_size,
            "staging_hours_note": "Multi-hour run_shark + AI_REVIEW_TICK_MINUTES not executed in this harness; "
            "use staging with the same runtime root and inspect the same files.",
        }
    finally:
        if saved_root is not None:
            os.environ["EZRAS_RUNTIME_ROOT"] = saved_root
        else:
            os.environ.pop("EZRAS_RUNTIME_ROOT", None)


def run_blocker_closure_bundle(
    runtime_root: Path,
    *,
    databank_root: Optional[Path] = None,
    scheduler_stress_ticks: int = 600,
) -> Dict[str, Any]:
    """
    Full closure bundle: connectivity → governance log → take-profit close → hard-stop close → tick stress.

    Writes ``BLOCKER_CLOSURE_REPORT.json`` and reuses ``run_full_proof`` governance log setup
    by calling ``run_full_proof`` first for base artifacts, **or** we compose manually.

    Implementation: call ``run_full_proof`` (includes governance scenarios + take_profit + short scheduler),
    then append hard-stop close on **same** roots, then long tick stress.
    """
    from trading_ai.runtime_proof.coinbase_shadow_paper_pass import (
        run_close_chain,
        run_full_proof,
        run_scheduler_probe,
    )

    runtime_root = runtime_root.resolve()
    databank_root = databank_root or (runtime_root / "databank")
    databank_root.mkdir(parents=True, exist_ok=True)

    public = coinbase_public_connectivity_probe("BTC-USD")

    base = run_full_proof(runtime_root, databank_root=databank_root)

    # Second close: hard stop (same MemoryStore file — second append)
    hs = run_close_chain(
        runtime_root,
        databank_root,
        trade_id="cb_hs_proof_001",
        hard_stop=True,
        product_id="ETH-USD",
    )

    sched_stress = run_scheduler_tick_stress(runtime_root, n_ticks=scheduler_stress_ticks)

    # APScheduler probe (registration / coalesce) — short wall-clock
    log_lines: List[str] = []
    sched_probe = run_scheduler_probe(runtime_root, databank_root, log_lines=log_lines)

    report = {
        "runtime_root": str(runtime_root),
        "coinbase_public_connectivity": public,
        "base_runtime_proof": {k: base[k] for k in ("governance", "close_chain", "scheduler", "log_lines") if k in base},
        "hard_stop_close_chain": hs,
        "scheduler_tick_stress": sched_stress,
        "scheduler_apscheduler_probe": sched_probe,
        "supabase_stance": "local_first_v1",
        "supabase_stance_doc": "docs/COINBASE_AVENUE_A_PRELIVE_DATA_STANCE.md",
    }

    rubric = evaluate_rubric(report)
    report["rubric"] = rubric
    out = runtime_root / "BLOCKER_CLOSURE_REPORT.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def evaluate_rubric(report: Dict[str, Any]) -> Dict[str, Any]:
    """pass / fail per blocker; overall GO only if all pass."""
    pub = report.get("coinbase_public_connectivity") or {}
    base_cc = (report.get("base_runtime_proof") or {}).get("close_chain") or {}
    hs = report.get("hard_stop_close_chain") or {}
    stress = report.get("scheduler_tick_stress") or {}
    sched = report.get("scheduler_apscheduler_probe") or {}

    # 1 Paper/sandbox: public OK + base close merged trade + no missing id
    m1 = base_cc.get("merged_trade") or {}
    paper_pass = (
        bool(pub.get("ok"))
        and m1.get("trade_id")
        and str(m1.get("avenue") or "").lower() == "coinbase"
        and base_cc.get("nte_entry_gate", {}).get("ok") is True
    )

    # 2 Long-run: stress harness OK + APScheduler probe OK (multi-hour = manual)
    sched_pass = bool(stress.get("ok")) and bool(sched.get("ok")) and sched.get("single_ai_review_tick_id") is True

    # 3 Hard stop
    hs_m = hs.get("merged_trade") or {}
    hs_pass = (
        hs.get("hard_stop") is True
        and str(hs.get("exit_reason") or "") == "stop_loss"
        and int(hs.get("packet_hard_stop_events") or 0) >= 1
        and (hs_m.get("hard_stop_exit") is True or str(hs_m.get("exit_reason") or "") == "stop_loss")
    )

    # 4 Supabase: Option B documented on disk
    doc = Path(__file__).resolve().parents[3] / "docs" / "COINBASE_AVENUE_A_PRELIVE_DATA_STANCE.md"
    supabase_pass = report.get("supabase_stance") == "local_first_v1" and doc.is_file()

    rubric = {
        "1_paper_sandbox_session": "pass" if paper_pass else "fail",
        "2_long_run_scheduler": "pass" if sched_pass else "fail",
        "3_hard_stop": "pass" if hs_pass else "fail",
        "4_supabase_stance": "pass" if supabase_pass else "fail",
    }
    all_pass = all(v == "pass" for v in rubric.values())
    rubric["overall"] = "GO_CONTROLLED_FIRST_20_CONSIDERATION" if all_pass else "NO_GO"
    rubric["notes"] = {
        "multi_hour_staging": "Not automated; required for full production sign-off if policy demands wall-clock proof.",
        "broker_signed_order": "This harness does not post an authenticated Advanced Trade order; evidence is "
        "public spot + local organism pipeline. For strict broker-only sign-off, run NTE/Shark in paper with "
        "credentials and archive the same artifact bundle.",
        "rubric_interpretation": "Pass here means automated/local evidence only; ops may still require "
        "wall-clock staging and broker archives before capital.",
    }
    return rubric
