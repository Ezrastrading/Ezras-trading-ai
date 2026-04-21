"""
Strict artifact-based judge for first-20 Coinbase Avenue A shadow sessions.

Reads on-disk artifacts only — does not assume the session "looked okay."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_proof.agnostic_verification import evaluate_organism_agnostic_lock, final_readiness_flags


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _index_jsonl_by_trade_id(path: Path) -> Dict[str, Dict[str, Any]]:
    rows, _ = _load_jsonl(path)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        tid = str(r.get("trade_id") or "").strip()
        if tid:
            out[tid] = r
    return out


def _build_universal_candidate_integrity(runtime_root: Path, trade_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Artifact-only checks for universal gap candidate + execution grade coverage."""
    root = runtime_root.resolve()
    tm = root / "data" / "trades" / "trades_master.jsonl"
    te = root / "data" / "trades" / "trades_edge_snapshot.jsonl"
    tx = root / "data" / "trades" / "trades_execution_snapshot.jsonl"
    by_m = _index_jsonl_by_trade_id(tm)
    by_e = _index_jsonl_by_trade_id(te)
    by_x = _index_jsonl_by_trade_id(tx)
    failure_categories_by_trade: Dict[str, List[str]] = {}
    for t in trade_rows:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("trade_id") or "").strip()
        if not tid:
            continue
        fails: List[str] = []
        master = by_m.get(tid) or {}
        edge = by_e.get(tid) or {}
        exe = by_x.get(tid) or {}
        ugc = master.get("universal_gap_candidate")
        if isinstance(ugc, dict) and ugc.get("must_trade") is True:
            cid = str(edge.get("candidate_id") or edge.get("universal_gap_candidate_id") or "").strip()
            if not cid:
                fails.append("missing_candidate")
        if not str(exe.get("execution_grade") or exe.get("grade") or "").strip():
            fails.append("missing_execution_grade")
        if fails:
            failure_categories_by_trade[tid] = fails
    return {
        "failure_categories_by_trade": failure_categories_by_trade,
        "runtime_root": str(root),
    }


def _load_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    out: List[Dict[str, Any]] = []
    bad = 0
    if not path.is_file():
        return out, 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            bad += 1
    return out, bad


def judge_first_twenty_session(archive_dir: Path) -> Dict[str, Any]:
    """
    ``archive_dir`` = session archive (contains ``session_manifest.json``, ``first_20_session_report.json``, etc.).
    """
    arch = archive_dir.resolve()
    rep = _load_json(arch / "first_20_session_report.json")
    man = _load_json(arch / "session_manifest.json")
    final = _load_json(arch / "first_20_session_report.final.json")

    missing: List[str] = []
    for name in ("first_20_session_report.json", "session_manifest.json"):
        if not (arch / name).is_file():
            missing.append(name)

    trades = (rep or {}).get("trades") or []
    trade_ids = [str(t.get("trade_id") or "") for t in trades if isinstance(t, dict)]
    completed_rows = [t for t in trades if t.get("status") != "entry_blocked"]

    # Trade memory / databank if bundled
    tm = _load_json(arch / "bundle_trade_memory.json")
    te_lines, te_bad = _load_jsonl(arch / "bundle_trade_events.jsonl")

    runtime_root = Path((man or {}).get("runtime_root") or arch)
    glog = runtime_root / "governance_gate_decisions.log"
    gov_attempts = runtime_root / "first_20_governance_attempts.jsonl"
    _, gov_parse_errs = _load_jsonl(gov_attempts) if gov_attempts.is_file() else ([], 0)

    ticks_path = runtime_root / "shark" / "memory" / "global" / "review_scheduler_ticks.jsonl"
    _, tick_bad = _load_jsonl(ticks_path)

    # Chain integrity
    clean = 0
    partial = 0
    broken = 0
    for t in completed_rows:
        tid = str(t.get("trade_id") or "")
        if not tid:
            broken += 1
            continue
        ok_fed = t.get("federation_included") is True
        ok_pkt = t.get("packet_inclusion_confirmed") is True
        if ok_fed and ok_pkt:
            clean += 1
        elif ok_fed or ok_pkt:
            partial += 1
        else:
            broken += 1

    # Rubric A–J
    def tri(ok: bool, risky: bool) -> str:
        if ok and not risky:
            return "PASS"
        if ok and risky:
            return "PARTIAL"
        return "FAIL"

    cum = (rep or {}).get("cumulative") or {}
    preflight_ok = man is not None and not missing
    A = tri(preflight_ok, False)

    entry_ok = all(
        ("governance_decision_reason" in t or t.get("status") == "entry_blocked")
        for t in trades
        if isinstance(t, dict)
    )
    B = tri(entry_ok, int(cum.get("gate_anomalies") or 0) > 0)

    close_ok = all(t.get("federation_included") for t in completed_rows)
    C = tri(close_ok, int(cum.get("process_closed_local_failures") or 0) > 0)

    art_ok = not missing and te_bad == 0
    D = tri(art_ok, len(missing) > 0)

    sched_ok = tick_bad == 0
    E = tri(sched_ok, tick_bad > 0)

    fed_ok = (rep or {}).get("cumulative", {}).get("federation_conflict_total", 0) == 0
    F = tri(fed_ok, not fed_ok)

    pkt_ok = all(t.get("packet_inclusion_confirmed") for t in completed_rows)
    G = tri(pkt_ok, not pkt_ok)

    rb = (rep or {}).get("rollback_active")
    H = tri(rb is None, rb is not None)

    obs_ok = gov_attempts.is_file() or glog.is_file()
    I = tri(obs_ok, not obs_ok)

    rec = (final or rep or {}).get("recommendation") or ""
    overall_j = "PASS" if rec == "PASS_SHADOW_VERIFICATION" else ("PARTIAL" if "PARTIAL" in str(rec) else "FAIL")
    J = overall_j

    rr = Path(str((man or {}).get("runtime_root") or arch))
    agnostic_ev = evaluate_organism_agnostic_lock(runtime_root=rr, run_tests=True)
    readiness = final_readiness_flags(agnostic_ev)
    if any(
        readiness.get(k)
        for k in ("strategy_dependency_detected", "latency_dependency_detected", "edge_dependency_detected")
    ):
        organism_status = "FAILED"
    elif readiness.get("organism_agnostic") and agnostic_ev.get("agnosticity_verified"):
        organism_status = "FULLY_AGNOSTIC"
    elif agnostic_ev.get("agnosticity_verified") or agnostic_ev.get("agnostic_unit_tests_passed"):
        organism_status = "PARTIAL"
    else:
        organism_status = "FAILED"
    K = "PASS" if organism_status == "FULLY_AGNOSTIC" else ("PARTIAL" if organism_status == "PARTIAL" else "FAIL")

    rubric = {
        "A_preflight_integrity": A,
        "B_entry_gate_integrity": B,
        "C_close_path_integrity": C,
        "D_artifact_integrity": D,
        "E_scheduler_integrity": E,
        "F_federation_integrity": F,
        "G_packet_review_integrity": G,
        "H_rollback_behavior": H,
        "I_operator_observability": I,
        "J_overall_avenue_a_shadow": J,
        "K_agnostic_organism_lock": K,
    }

    # GO / NO-GO (strict)
    strict_go = (
        rec == "PASS_SHADOW_VERIFICATION"
        and clean == len(completed_rows)
        and broken == 0
        and tick_bad == 0
        and te_bad == 0
        and not missing
    )
    strict_go_agnostic = strict_go and organism_status == "FULLY_AGNOSTIC"
    capital = "NO_GO" if not strict_go else "GO_FOR_CONTROLLED_FIRST_20_LIVE_CONSIDERATION"
    if strict_go and organism_status != "FULLY_AGNOSTIC":
        capital = "NO_GO_ORGANISM_NOT_FULLY_AGNOSTIC"

    universal_candidate_integrity = _build_universal_candidate_integrity(rr, trades)

    return {
        "archive_dir": str(arch),
        "missing_artifacts": missing,
        "session_completeness": {
            "manifest_present": man is not None,
            "report_present": rep is not None,
            "trades_recorded": len(trades),
            "completed_trades": len(completed_rows),
        },
        "trade_chain": {
            "fully_clean": clean,
            "partially_documented": partial,
            "broken_chain": broken,
            "trade_ids": trade_ids,
        },
        "parsing": {
            "trade_events_jsonl_bad_lines": te_bad,
            "scheduler_ticks_bad_lines": tick_bad,
            "governance_attempts_lines": gov_parse_errs,
        },
        "rubric": rubric,
        "recommendation_from_session": rec,
        "real_capital_go_no_go": capital,
        "organism_status": organism_status,
        "agnosticity_verified": bool(agnostic_ev.get("agnosticity_verified")),
        "readiness_flags": readiness,
        "organism_agnostic_lock": agnostic_ev,
        "strict_go_criteria": {
            "requires": [
                "PASS_SHADOW_VERIFICATION",
                "no broken chain rows",
                "no JSONL parse errors in scheduler/ticks bundle",
                "all artifacts present",
            ]
        },
        "strict_go_with_agnostic_lock": strict_go_agnostic,
        "universal_candidate_integrity": universal_candidate_integrity,
    }


def write_judge_report(archive_dir: Path, out_name: str = "first_20_judge_report.json") -> Path:
    j = judge_first_twenty_session(archive_dir)
    p = archive_dir / out_name
    p.write_text(json.dumps(j, indent=2, default=str), encoding="utf-8")
    return p
