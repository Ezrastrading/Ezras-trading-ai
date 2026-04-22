"""Explicit lifecycle integration for the multi-avenue universal layer — each hook logs traceably."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from trading_ai.multi_avenue.auto_scaffold import ensure_all_registered_scaffolds, ensure_avenue_scaffold, ensure_gate_scaffold
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.multi_avenue.honest_not_live import write_honest_not_live_matrix
from trading_ai.multi_avenue.operational_proof import record_operational_proof
from trading_ai.multi_avenue.scoped_paths import avenue_review_dir, gate_review_dir
from trading_ai.multi_avenue.system_rollup_engine import write_system_rollup_snapshot
from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle
from trading_ai.runtime_paths import ezras_runtime_root


def _root(rt: Optional[Path]) -> Path:
    return Path(rt or ezras_runtime_root()).resolve()


def _log_hook(name: str, **extra: Any) -> None:
    append_control_events("lifecycle_hook_log.json", {"hook": name, **extra})


def infer_trade_scope(trade: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort avenue/gate from trade dict — never guesses across venues without markers."""
    aid = trade.get("avenue_id")
    gid = trade.get("gate_id")
    if aid and gid:
        return str(aid), str(gid)
    outlet = str(trade.get("outlet") or trade.get("platform") or "").lower()
    tg = str(trade.get("trading_gate") or trade.get("gate") or "").strip()
    gid2 = str(gid or tg or "").strip() or None
    aid2: Optional[str] = str(aid).strip() if aid else None
    if not aid2 and outlet:
        try:
            from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions

            candidates: list[str] = []
            for av in merged_avenue_definitions():
                vname = str(av.get("venue_name") or "").lower().strip()
                if vname and vname in outlet:
                    candidates.append(str(av.get("avenue_id")))
            if len(candidates) == 1:
                aid2 = candidates[0]
        except Exception:
            aid2 = None
    return aid2, gid2


def on_trade_open(trade: Optional[Dict[str, Any]], *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Attach ratio context + scope labels; update progression/edge/PnL summaries (scoped stubs)."""
    root = _root(runtime_root)
    if not trade:
        _log_hook("on_trade_open", status="skipped", reason="empty_trade")
        return {"status": "skipped", "reason": "empty_trade"}
    aid, gid = infer_trade_scope(trade)
    _log_hook("on_trade_open", trade_id=trade.get("trade_id"), avenue_id=aid, gate_id=gid)
    if not aid or not gid:
        return {"status": "partial", "reason": "could_not_infer_scope", "avenue_id": aid, "gate_id": gid}
    ensure_gate_scaffold(aid, gid, runtime_root=root)
    gd = gate_review_dir(aid, gid, runtime_root=root)
    ratio = gd / "ratio_view" / "ratio_context.json"
    ratio.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scope_level": "gate",
        "avenue_id": aid,
        "gate_id": gid,
        "trade_id": trade.get("trade_id"),
        "ratio_context_stub": True,
        "labels": {"source": "lifecycle_hooks.on_trade_open"},
    }
    ratio.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    edge = gd / "edge_registry" / "last_open_hint.json"
    edge.parent.mkdir(parents=True, exist_ok=True)
    edge.write_text(json.dumps({**payload, "edge_stats_stub": True}, indent=2, default=str), encoding="utf-8")
    try:
        from trading_ai.learning.self_learning_engine import run_self_learning_engine

        run_self_learning_engine("trade_open", trade, runtime_root=root)
        from trading_ai.learning.self_learning_engine import run_daily_learning_if_needed

        run_daily_learning_if_needed(runtime_root=root)
    except Exception:
        pass
    return {"status": "ok", "avenue_id": aid, "gate_id": gid, "paths": [str(ratio), str(edge)]}


def on_trade_close(trade: Optional[Dict[str, Any]], *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Update PnL aggregates + edge lifecycle + milestone hints (scoped stubs)."""
    root = _root(runtime_root)
    if not trade:
        _log_hook("on_trade_close", status="skipped", reason="empty_trade")
        return {"status": "skipped", "reason": "empty_trade"}
    aid, gid = infer_trade_scope(trade)
    _log_hook("on_trade_close", trade_id=trade.get("trade_id"), avenue_id=aid, gate_id=gid)
    if not aid or not gid:
        return {"status": "partial", "reason": "could_not_infer_scope"}
    gd = gate_review_dir(aid, gid, runtime_root=root)
    pnl = gd / "edge_summary" / "last_close_pnl_hint.json"
    pnl.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "scope_level": "gate",
        "avenue_id": aid,
        "gate_id": gid,
        "trade_id": trade.get("trade_id"),
        "result": trade.get("result"),
        "pnl_aggregate_stub": True,
    }
    pnl.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    mil = avenue_review_dir(aid, runtime_root=root) / "progression" / "milestone_progress_hint.json"
    mil.parent.mkdir(parents=True, exist_ok=True)
    mil.write_text(json.dumps({"avenue_id": aid, "milestone_progress_stub": True}, indent=2, default=str), encoding="utf-8")
    try:
        from trading_ai.learning.self_learning_engine import run_self_learning_engine

        run_self_learning_engine("trade_close", trade, runtime_root=root)
        from trading_ai.learning.self_learning_engine import run_daily_learning_if_needed

        run_daily_learning_if_needed(runtime_root=root)
    except Exception:
        pass
    return {"status": "ok", "paths": [str(pnl), str(mil)]}


def on_validation(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Preflight: scaffolds + control bundle + status matrix."""
    root = _root(runtime_root)
    _log_hook("on_validation")
    sc = ensure_all_registered_scaffolds(runtime_root=root)
    bundle = write_multi_avenue_control_bundle(runtime_root=root)
    record_operational_proof("validation_bundle", detail={"scaffold_summary_keys": list(sc.keys())}, runtime_root=root)
    try:
        from trading_ai.learning.self_learning_engine import run_self_learning_engine, run_daily_learning_if_needed

        run_self_learning_engine("validation", {"phase": "preflight"}, runtime_root=root)
        run_daily_learning_if_needed(runtime_root=root)
    except Exception:
        pass
    return {"status": "ok", "scaffold": sc, "bundle_keys": list(bundle.keys())}


def on_readiness(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Readiness: summaries + honesty matrix + rollup snapshot."""
    root = _root(runtime_root)
    _log_hook("on_readiness")
    h = write_honest_not_live_matrix(runtime_root=root)
    r = write_system_rollup_snapshot(runtime_root=root)
    bundle = write_multi_avenue_control_bundle(runtime_root=root)
    record_operational_proof("readiness_refresh", runtime_root=root)
    try:
        from trading_ai.learning.self_learning_engine import run_self_learning_engine

        run_self_learning_engine("readiness", {}, runtime_root=root)
    except Exception:
        pass
    return {"status": "ok", "honest_not_live_matrix": h, "system_rollup_snapshot": r, "bundle_keys": list(bundle.keys())}


def on_daily_cycle(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Daily review: refresh CEO shells + progression + ratios via control bundle."""
    root = _root(runtime_root)
    _log_hook("on_daily_cycle")
    out = write_multi_avenue_control_bundle(runtime_root=root)
    record_operational_proof("daily_cycle_bundle", runtime_root=root)
    # Mission/goals must be active operating drivers: compute pace + seed next actions.
    # This remains advisory to execution (does not bypass gates), but actively influences queues.
    try:
        from trading_ai.global_layer.mission_goals_operating_layer import refresh_mission_goals_operating_layer
        from trading_ai.global_layer.mission_goals_task_consumer import consume_mission_goals_into_tasks
        from trading_ai.global_layer.trade_truth import load_federated_trades

        trades, meta = load_federated_trades()
        _ = trades
        # Use best-effort total balance if present; otherwise default to mission start balance.
        total = float((meta or {}).get("total_balance_usd") or (meta or {}).get("total_balance") or 200.0)
        refresh_mission_goals_operating_layer(total_balance_usd=total, runtime_root=root)
        # Consume those priorities into real orchestration tasks (bot/gate/avenue intake).
        consume_mission_goals_into_tasks(runtime_root=root)
    except Exception:
        pass
    try:
        from trading_ai.learning.lockdown_bundle import refresh_lockdown_artifacts
        from trading_ai.learning.self_learning_engine import run_daily_learning_if_needed, run_self_learning_engine

        run_daily_learning_if_needed(runtime_root=root)
        refresh_lockdown_artifacts(runtime_root=root)
        run_self_learning_engine("daily_cycle", {}, runtime_root=root)
    except Exception:
        pass
    try:
        from trading_ai.intelligence.edge_research.daily_cycle import run_daily_edge_research_cycle

        run_daily_edge_research_cycle(runtime_root=root)
    except Exception:
        pass
    try:
        from trading_ai.control.first_60_day_ops import (
            ensure_first_60_day_control_artifacts,
            write_first_60_day_daily_envelope,
            write_first_60_day_weekly_envelope_if_due,
        )

        ensure_first_60_day_control_artifacts(runtime_root=root)
        diag_path = root / "data" / "review" / "daily_diagnosis.json"
        diag: Optional[Dict[str, Any]] = None
        if diag_path.is_file():
            try:
                raw = json.loads(diag_path.read_text(encoding="utf-8"))
                diag = raw if isinstance(raw, dict) else None
            except (json.JSONDecodeError, OSError):
                diag = None
        write_first_60_day_daily_envelope(diag or {}, runtime_root=root, skip_if_same_day=False)
        write_first_60_day_weekly_envelope_if_due(runtime_root=root)
    except Exception:
        pass
    return {"status": "ok", "bundle_keys": list(out.keys())}


def on_scanner_cycle(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Scanner cycle: refresh scanner framework artifacts (no fake scanner execution)."""
    root = _root(runtime_root)
    _log_hook("on_scanner_cycle")
    out = write_multi_avenue_control_bundle(runtime_root=root)
    return {"status": "ok", "bundle_keys": list(out.keys())}


def on_scanner_cycle_export(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Scanner cycle + durable ``scanner_autonomy_snapshot.json`` for autonomy proofs.

    No live venue calls; refreshes control scaffolds only.
    """
    root = _root(runtime_root)
    inner = on_scanner_cycle(runtime_root=root)
    seq = int(datetime.now(timezone.utc).timestamp() * 1000) % (10**9)
    p = root / "data" / "control" / "scanner_autonomy_snapshot.json"
    prev_gap: Optional[float] = None
    try:
        if p.is_file():
            prev = json.loads(p.read_text(encoding="utf-8"))
            raw = str((prev or {}).get("generated_at") or "").strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            prev_dt = datetime.fromisoformat(raw) if raw else None
            if prev_dt and prev_dt.tzinfo is None:
                prev_dt = prev_dt.replace(tzinfo=timezone.utc)
            if prev_dt is not None:
                prev_gap = max(0.0, (datetime.now(timezone.utc) - prev_dt).total_seconds())
    except Exception:
        prev_gap = None
    # Optional: Gate B (Coinbase gainers snapshot) — public market tickers only, no orders.
    gb: Optional[Dict[str, Any]] = None
    try:
        gb_enabled = (os.environ.get("GATE_B_LIVE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    except Exception:
        gb_enabled = False
    if gb_enabled:
        try:
            from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import run_gate_b_gainers_selection
            from trading_ai.global_layer.review_storage import ReviewStorage

            # Writes data/control/gate_b_selection_snapshot.json
            gb = run_gate_b_gainers_selection(runtime_root=root, client=None)

            # Publish lightweight candidate queue items for the global layer.
            st = ReviewStorage()
            cq = st.load_json("candidate_queue.json")
            items = list(cq.get("items") or [])
            for pid in list(gb.get("selected_symbols") or [])[:8]:
                items.append(
                    {
                        "id": f"gate_b_{pid}_{int(time.time())}",
                        "ts": time.time(),
                        "avenue_id": "B",
                        "gate_id": "gate_b",
                        "product_id": pid,
                        "source": "gate_b_gainers_selection",
                        "status": "new",
                        "evidence_ref": "data/control/gate_b_selection_snapshot.json",
                    }
                )
            cq["items"] = items[-300:]
            st.save_json("candidate_queue.json", cq)

            # Truthful gate-local scanner metadata: active only if snapshot selected or evaluable candidates exist.
            try:
                gr = gate_review_dir("B", "gate_b", runtime_root=root)
                mp = gr / "scanner_metadata.json"
                present = bool((gb.get("ranked_gainer_candidates") or []))
                active = present
                meta = {
                    "artifact": "scanner_metadata",
                    "avenue_id": "B",
                    "gate_id": "gate_b",
                    "scanner_framework_ready": True,
                    "active_scanners_present": bool(active),
                    "scanner_kind": "coinbase_public_tickers",
                    "last_scan_ts": time.time(),
                    "selection_state": (gb.get("selection_summary") or {}).get("gate_b_selection_state"),
                    "selected_symbols": gb.get("selected_symbols") or [],
                    "honesty": "Active=true means the scanner executed and produced a snapshot; it does not imply any candidate passed policy.",
                }
                mp.write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
            except Exception:
                pass
        except Exception as exc:
            gb = {"ok": False, "error": type(exc).__name__}

    snap = {
        "truth_version": "scanner_autonomy_snapshot_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_seq": seq,
        "hook_status": inner.get("status"),
        "seconds_since_prior_snapshot": prev_gap,
        "anti_idle_rescan_recommended": bool(prev_gap is not None and prev_gap > 60.0),
        "gate_b_gainers_snapshot": gb,
        "honesty": (
            "Control scaffold refresh. When GATE_B_LIVE_EXECUTION_ENABLED=true, also performs "
            "unauthenticated Coinbase market ticker reads for Gate B gainers snapshot (no orders)."
        ),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {**inner, "scan_seq": seq}


def on_avenue_registered(avenue_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """After registry append — scaffold avenue immediately."""
    root = _root(runtime_root)
    _log_hook("on_avenue_registered", avenue_id=avenue_id)
    out = ensure_avenue_scaffold(avenue_id, runtime_root=root)
    try:
        from trading_ai.global_layer.bot_hierarchy.registry import ensure_avenue_master, ensure_avenue_worker_role
        from trading_ai.global_layer.bot_hierarchy.role_registry import iter_default_avenue_roles

        aid = str(avenue_id).strip()
        ensure_avenue_master(aid)
        for spec in iter_default_avenue_roles():
            # Avenue master is represented by the dedicated bot type; do not duplicate as a worker.
            if str(spec.role).strip() == "avenue_master":
                continue
            ensure_avenue_worker_role(
                aid,
                role=spec.role,
                evidence_contract=spec.evidence_contract,
                truth_source=spec.truth_source,
            )
    except Exception:
        pass
    return out


def on_gate_registered(avenue_id: str, gate_id: str, *, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """After gate registration — scaffold gate immediately."""
    root = _root(runtime_root)
    _log_hook("on_gate_registered", avenue_id=avenue_id, gate_id=gate_id)
    out = ensure_gate_scaffold(avenue_id, gate_id, runtime_root=root)
    try:
        from trading_ai.global_layer.bot_hierarchy.registry import ensure_gate_worker_role
        from trading_ai.global_layer.bot_hierarchy.role_registry import iter_default_gate_roles

        for spec in iter_default_gate_roles():
            ensure_gate_worker_role(
                str(avenue_id).strip(),
                str(gate_id).strip(),
                role=spec.role,
                evidence_contract=spec.evidence_contract,
                truth_source=spec.truth_source,
            )
    except Exception:
        pass
    return out


def on_system_boot(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Process start: same as validation preflight (scaffolds + control bundle)."""
    root = _root(runtime_root)
    _log_hook("on_system_boot")
    return on_validation(runtime_root=root)
