"""
Autonomous non-live operating system (two-server role design).

This module provides:
- ROLE=ops tick/daemon: fast, execution-adjacent loops (scan/eval/outcomes/safety/metrics)
- ROLE=research tick/daemon: heavy governance loops (research/learning/audits/CEO/review/queues)

Safety: this operating system is *non-live by default* and will refuse to run if live execution
flags are enabled.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root

ServerRole = Literal["ops", "research"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_root(runtime_root: Optional[Path]) -> Path:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    return root


def _lock_path(root: Path) -> Path:
    return root / "data" / "control" / "server_role_locks.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)

def _os_dir(root: Path) -> Path:
    p = root / "data" / "control" / "operating_system"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _role_status_path(root: Path, role: ServerRole) -> Path:
    return _os_dir(root) / f"loop_status_{role}.json"


def _loop_result_path(root: Path, role: ServerRole, loop_id: str) -> Path:
    d = _os_dir(root) / "loops" / role
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{loop_id}.json"


@dataclass(frozen=True)
class LoopSpec:
    loop_id: str
    owner_role: ServerRole
    interval_sec: float
    runner: Callable[[Path], Dict[str, Any]]


def role_contract() -> Dict[str, Any]:
    """
    Firm-grade role contract: explicit loop ownership and cadence.
    Written for operator visibility; enforced by role locks + per-role supervisors.
    """
    return {
        "truth_version": "operating_system_role_contract_v1",
        "generated_at": _iso(),
        "roles": {
            "ops": {
                "purpose": "realtime ops / scanning / execution-adjacent loops (non-live)",
                "owns": [
                    "validation_bootstrap",
                    "scanner_cycle",
                    "outcome_ingestion",
                    "fast_health_snapshot",
                    "fast_regression_drift",
                ],
            },
            "research": {
                "purpose": "research / governance / learning / planning loops (non-live)",
                "owns": [
                    "daily_cycle",
                    "review_cycle",
                    "ceo_daily_review",
                    "pnl_review",
                    "comparisons",
                    "trade_cycle_intelligence",
                    "promotion_capital_cycle",
                    "learning_distillation_snapshot",
                ],
            },
        },
        "handoff": {
            "durable_inputs": [
                "orchestration queues (research/experiment/implementation/validation)",
                "tasks.jsonl (shadow routing)",
                "federated trade truth artifacts",
            ],
            "collision_prevention": [
                "role locks (ops/research) per runtime_root",
                "canonical truth writers per domain (promotion/capital/review)",
            ],
        },
        "safety": "Operating system enforces non-live env defaults and blocks if live env is detected.",
    }


def _safety_assert_non_live() -> Tuple[bool, str]:
    """
    Hard safety gate for this OS.

    Non-negotiable: do not run if live execution env is enabled.
    """
    nte_mode = (os.environ.get("NTE_EXECUTION_MODE") or os.environ.get("EZRAS_MODE") or "paper").strip().lower()
    nte_live = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    cb_enabled = (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    if nte_mode in ("live", "production", "prod") or nte_live or cb_enabled:
        return False, "live_execution_env_detected"
    return True, "ok"


def enforce_non_live_env_defaults() -> None:
    """
    Set explicit non-live defaults (does not touch secrets).

    This prevents accidental live enablement in a long-running daemon shell.
    """
    os.environ.setdefault("NTE_EXECUTION_MODE", "paper")
    os.environ.setdefault("NTE_LIVE_TRADING_ENABLED", "false")
    os.environ.setdefault("NTE_PAPER_MODE", "true")
    os.environ.setdefault("NTE_DRY_RUN", "true")
    os.environ.setdefault("COINBASE_EXECUTION_ENABLED", "false")
    os.environ.setdefault("COINBASE_ENABLED", "false")
    os.environ.setdefault("NTE_EXECUTION_SCOPE", "paper")
    os.environ.setdefault("NTE_COINBASE_EXECUTION_ROUTE", "paper")


@dataclass(frozen=True)
class RoleLock:
    role: ServerRole
    holder_id: str
    acquired_at: str
    expires_at_unix: float


def try_acquire_role_lock(
    *,
    role: ServerRole,
    holder_id: str,
    runtime_root: Optional[Path] = None,
    ttl_seconds: float = 90.0,
) -> Tuple[bool, str, Optional[RoleLock]]:
    """
    Best-effort collision prevention:
    - one ops daemon per runtime root
    - one research daemon per runtime root
    """
    root = _runtime_root(runtime_root)
    p = _lock_path(root)
    now = time.time()
    cur = _read_json(p)
    locks = cur.get("locks") if isinstance(cur.get("locks"), dict) else {}
    row = locks.get(role) if isinstance(locks, dict) else None
    if isinstance(row, dict):
        try:
            exp = float(row.get("expires_at_unix") or 0.0)
        except Exception:
            exp = 0.0
        if exp > now and str(row.get("holder_id") or "") != str(holder_id):
            return False, f"role_lock_held:{role}", None
    lock = RoleLock(role=role, holder_id=str(holder_id), acquired_at=_iso(), expires_at_unix=now + float(ttl_seconds))
    locks = dict(locks) if isinstance(locks, dict) else {}
    locks[role] = {
        "role": lock.role,
        "holder_id": lock.holder_id,
        "acquired_at": lock.acquired_at,
        "expires_at_unix": lock.expires_at_unix,
    }
    payload = {
        "truth_version": "server_role_locks_v1",
        "updated_at": _iso(),
        "locks": locks,
        "honesty": "Best-effort lock to prevent two daemons per role on same runtime_root.",
    }
    _write_json(p, payload)
    return True, "ok", lock


def release_role_lock(*, role: ServerRole, holder_id: str, runtime_root: Optional[Path] = None) -> None:
    root = _runtime_root(runtime_root)
    p = _lock_path(root)
    cur = _read_json(p)
    locks = cur.get("locks") if isinstance(cur.get("locks"), dict) else {}
    if not isinstance(locks, dict):
        return
    row = locks.get(role)
    if isinstance(row, dict) and str(row.get("holder_id") or "") == str(holder_id):
        del locks[role]
        cur["locks"] = locks
        cur["updated_at"] = _iso()
        _write_json(p, cur)


def tick_ops_once(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    One ops tick (non-live): scaffolds, scanner hooks, outcomes ingestion, safety snapshot, metrics.
    """
    enforce_non_live_env_defaults()
    ok, why = _safety_assert_non_live()
    if not ok:
        return {"ok": False, "blocked": True, "reason": why}
    root = _runtime_root(runtime_root)

    steps: Dict[str, Any] = {"generated_at": _iso(), "role": "ops", "runtime_root": str(root)}
    try:
        from trading_ai.multi_avenue.lifecycle_hooks import on_scanner_cycle, on_validation

        steps["validation"] = on_validation(runtime_root=root)
        steps["scanner_cycle"] = on_scanner_cycle(runtime_root=root)
    except Exception as exc:
        steps["scanner_cycle"] = {"ok": False, "error": type(exc).__name__}

    # Outcome ingestion (best-effort): summarize federated trades and refresh per-scope lifecycle hints.
    try:
        from trading_ai.global_layer.trade_truth import load_federated_trades
        from trading_ai.multi_avenue.lifecycle_hooks import on_trade_close

        trades, meta = load_federated_trades()
        closed = [t for t in (trades or []) if isinstance(t, dict) and str(t.get("status") or "").lower() in ("closed", "settled")]
        # Touch a small number of close hooks to keep per-gate/per-avenue artifacts fresh (non-live).
        touched = 0
        for t in closed[-10:]:
            try:
                on_trade_close(t, runtime_root=root)
                touched += 1
            except Exception:
                pass
        snap = {
            "truth_version": "ops_outcome_ingestion_snapshot_v1",
            "generated_at": _iso(),
            "meta": meta or {},
            "trade_count": len(trades or []),
            "closed_count": len(closed),
            "close_hooks_touched": touched,
        }
        out_path = root / "data" / "control" / "ops_outcome_ingestion_snapshot.json"
        _write_json(out_path, snap)
        steps["outcome_ingestion"] = {"ok": True, "snapshot_path": str(out_path), "closed_count": len(closed)}
    except Exception as exc:
        steps["outcome_ingestion"] = {"ok": False, "error": type(exc).__name__}

    try:
        from trading_ai.global_layer.orchestration_truth_chain import write_orchestration_truth_chain

        steps["orchestration_truth_chain"] = {"blockers": (write_orchestration_truth_chain().get("blockers") or [])}
    except Exception as exc:
        steps["orchestration_truth_chain"] = {"ok": False, "error": type(exc).__name__}

    try:
        from trading_ai.ops.automation_heartbeat import record_heartbeat

        record_heartbeat("ops_tick", ok=True, note="tick_ops_once")
        steps["heartbeat"] = True
    except Exception:
        steps["heartbeat"] = False

    return {"ok": True, "steps": steps}


def tick_research_once(*, runtime_root: Optional[Path] = None, skip_models: bool = True) -> Dict[str, Any]:
    """
    One research tick (non-live): mission/goals cycle, queue consumption, CEO review, review scheduler tick,
    learning distillation, promotion/capital cycle (deterministic).
    """
    enforce_non_live_env_defaults()
    ok, why = _safety_assert_non_live()
    if not ok:
        return {"ok": False, "blocked": True, "reason": why}
    root = _runtime_root(runtime_root)
    steps: Dict[str, Any] = {"generated_at": _iso(), "role": "research", "runtime_root": str(root)}

    try:
        from trading_ai.multi_avenue.lifecycle_hooks import on_daily_cycle

        steps["daily_cycle"] = on_daily_cycle(runtime_root=root)
    except Exception as exc:
        steps["daily_cycle"] = {"ok": False, "error": type(exc).__name__}

    # Review cycle (non-live, no external model calls): run one stubbed review.
    try:
        from trading_ai.global_layer.review_scheduler import run_full_review_cycle

        rep = run_full_review_cycle("midday", skip_models=bool(skip_models))
        steps["review_cycle"] = {"ok": True, "kind": "midday", "packet_id": (rep.get("packet") or {}).get("packet_id")}
    except Exception as exc:
        steps["review_cycle"] = {"ok": False, "error": type(exc).__name__}

    try:
        from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review

        ceo = write_daily_ceo_review(estimated_review_tokens=50)
        steps["ceo_daily_review"] = {"truth_version": ceo.get("truth_version"), "bot_total": ceo.get("bot_total")}
    except Exception as exc:
        steps["ceo_daily_review"] = {"ok": False, "error": type(exc).__name__}

    try:
        from trading_ai.global_layer.deterministic_autonomous_orchestration import run_full_deterministic_cycle

        steps["promotion_capital_cycle"] = run_full_deterministic_cycle()
    except Exception as exc:
        steps["promotion_capital_cycle"] = {"ok": False, "error": type(exc).__name__}

    # Learning distillation (minimal, non-destructive): snapshot pending/shared lesson queues + seed tasks via mission consumer already wired.
    try:
        from trading_ai.global_layer._bot_paths import global_layer_governance_dir
        from trading_ai.global_layer.learning_distillation import load_shared_learning

        gov = global_layer_governance_dir()
        pend_p = gov / "shared_learning_pending.json"
        pend = _read_json(pend_p) if pend_p.is_file() else {"items": []}
        shared = load_shared_learning()
        dist = {
            "truth_version": "learning_distillation_snapshot_v1",
            "generated_at": _iso(),
            "pending_count": len(list((pend.get("items") or []))),
            "approved_shared_count": len(list((shared.get("lessons") or []))),
            "honesty": "Snapshot only; approval remains gated (no auto-approve).",
        }
        out_path = gov / "learning_distillation_snapshot.json"
        _write_json(out_path, dist)
        steps["learning_distillation"] = {"ok": True, "path": str(out_path), "pending": dist["pending_count"]}
    except Exception as exc:
        steps["learning_distillation"] = {"ok": False, "error": type(exc).__name__}

    try:
        from trading_ai.ops.automation_heartbeat import record_heartbeat

        record_heartbeat("research_tick", ok=True, note="tick_research_once")
        steps["heartbeat"] = True
    except Exception:
        steps["heartbeat"] = False

    return {"ok": True, "steps": steps}


def _ops_loops() -> List[LoopSpec]:
    def _validation_bootstrap(root: Path) -> Dict[str, Any]:
        from trading_ai.multi_avenue.lifecycle_hooks import on_validation
        return on_validation(runtime_root=root)

    def _scanner_cycle(root: Path) -> Dict[str, Any]:
        from trading_ai.multi_avenue.lifecycle_hooks import on_scanner_cycle
        return on_scanner_cycle(runtime_root=root)

    def _outcome_ingestion(root: Path) -> Dict[str, Any]:
        # Use tick_ops_once ingestion result as loop body, but return the artifact path.
        out = tick_ops_once(runtime_root=root)
        return out.get("steps", {}).get("outcome_ingestion") or {"ok": False, "error": "missing_outcome_ingestion"}

    def _fast_health(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.orchestration_truth_chain import write_orchestration_truth_chain
        chain = write_orchestration_truth_chain()
        return {"blockers": list(chain.get("blockers") or []), "truth_version": chain.get("truth_version")}

    def _fast_regression(root: Path) -> Dict[str, Any]:
        # Minimal fast drift/regression: compare rolling_7d across snapshots.
        from trading_ai.global_layer.trade_truth import load_federated_trades
        from trading_ai.global_layer.pnl_aggregator import aggregate_from_trades

        trades, _meta = load_federated_trades()
        agg = aggregate_from_trades(list(trades or []))
        p = root / "data" / "control" / "ops_regression_drift.json"
        prev = _read_json(p)
        now7 = float(agg.get("rolling_7d_net_usd") or 0.0)
        prev7 = float((prev.get("rolling_7d_net_usd") if isinstance(prev, dict) else 0.0) or 0.0)
        delta = now7 - prev7
        verdict = "stable"
        if delta < -25.0:
            verdict = "degrading"
        elif delta > 25.0:
            verdict = "improving"
        payload = {
            "truth_version": "ops_regression_drift_v1",
            "generated_at": _iso(),
            "rolling_7d_net_usd": now7,
            "delta_vs_prev_snapshot": round(delta, 6),
            "verdict": verdict,
            "honesty": "Fast heuristic from federated trades only; research server performs deeper comparisons.",
        }
        _write_json(p, payload)
        return payload

    return [
        LoopSpec("validation_bootstrap", "ops", 300.0, _validation_bootstrap),
        LoopSpec("scanner_cycle", "ops", 20.0, _scanner_cycle),
        LoopSpec("outcome_ingestion", "ops", 30.0, _outcome_ingestion),
        LoopSpec("fast_health_snapshot", "ops", 30.0, _fast_health),
        LoopSpec("fast_regression_drift", "ops", 60.0, _fast_regression),
    ]


def _research_loops(*, skip_models: bool) -> List[LoopSpec]:
    def _daily_cycle(root: Path) -> Dict[str, Any]:
        from trading_ai.multi_avenue.lifecycle_hooks import on_daily_cycle
        return on_daily_cycle(runtime_root=root)

    def _review_cycle(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.review_scheduler import run_full_review_cycle
        rep = run_full_review_cycle("midday", skip_models=bool(skip_models))
        return {"packet_id": (rep.get("packet") or {}).get("packet_id"), "ok": True}

    def _ceo(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.ceo_daily_orchestration import write_daily_ceo_review
        ceo = write_daily_ceo_review(estimated_review_tokens=50)
        return {"truth_version": ceo.get("truth_version"), "bot_total": ceo.get("bot_total")}

    def _pnl_review(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.trade_truth import load_federated_trades
        from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
        from trading_ai.global_layer.pnl_aggregator import refresh_global_pnl_files

        trades, _meta = load_federated_trades()
        store = GlobalMemoryStore()
        refresh_global_pnl_files(store, list(trades or []))
        daily = store.load_json("daily_pnl_summary.json")
        weekly = store.load_json("weekly_pnl_summary.json")
        payload = {
            "truth_version": "pnl_review_v1",
            "generated_at": _iso(),
            "daily_net_usd": float(daily.get("period_net_usd") or 0.0),
            "weekly_net_usd": float(weekly.get("period_net_usd") or 0.0),
            "by_avenue": daily.get("by_avenue") or {},
        }
        out = root / "data" / "control" / "pnl_review.json"
        _write_json(out, payload)
        # Behavioral consumption: if weekly is negative, create high-priority risk task.
        try:
            from trading_ai.global_layer.task_router import route_task_shadow
            from trading_ai.global_layer.bot_types import BotRole
            from trading_ai.global_layer.bot_registry import load_registry

            reg = load_registry()
            scopes = {(str(b.get("avenue") or "A"), str(b.get("gate") or "none")) for b in (reg.get("bots") or []) if isinstance(b, dict)}
            scopes = scopes or {("A", "none")}
            if float(payload["weekly_net_usd"]) < 0:
                for av, gate in scopes:
                    t = route_task_shadow(
                        avenue=av,
                        gate=gate,
                        task_type="pnl_review::risk_reduction",
                        source_bot_id="research_os",
                        role=BotRole.RISK.value,
                        evidence_ref=str(out),
                    )
                    t["priority"] = int(t.get("priority") or 0) + 200
        except Exception:
            pass
        return payload

    def _comparisons(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.trade_truth import load_federated_trades
        from trading_ai.intelligence.avenue_performance import compute_avenue_performance
        from trading_ai.global_layer.task_router import route_task_shadow
        from trading_ai.global_layer.bot_types import BotRole
        from trading_ai.global_layer.bot_registry import load_registry

        trades, _meta = load_federated_trades()
        perf = compute_avenue_performance(list(trades or []))
        out = root / "data" / "control" / "performance_comparisons.json"
        payload = {"truth_version": "performance_comparisons_v1", "generated_at": _iso(), "avenue_performance": perf}
        _write_json(out, payload)
        # Behavioral consumption: route tasks biased toward weakest avenue.
        weakest = str(perf.get("weakest_avenue") or "")
        reg = load_registry()
        scopes = {(str(b.get("avenue") or "A"), str(b.get("gate") or "none")) for b in (reg.get("bots") or []) if isinstance(b, dict)}
        scopes = scopes or {("A", "none")}
        for av, gate in scopes:
            boost = 120 if weakest and str(av) == weakest else 40
            t = route_task_shadow(
                avenue=av,
                gate=gate,
                task_type="comparisons::avenue",
                source_bot_id="research_os",
                role=BotRole.LEARNING.value,
                evidence_ref=str(out),
            )
            t["priority"] = int(t.get("priority") or 0) + boost
        return payload

    def _trade_cycle_intel(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.trade_cycle_intelligence import refresh_trade_cycle_intelligence_bundle
        return refresh_trade_cycle_intelligence_bundle(root)

    def _promo_capital(root: Path) -> Dict[str, Any]:
        from trading_ai.global_layer.deterministic_autonomous_orchestration import run_full_deterministic_cycle
        return run_full_deterministic_cycle()

    def _learning_snapshot(root: Path) -> Dict[str, Any]:
        # Reuse tick_research_once logic for learning distillation snapshot.
        out = tick_research_once(runtime_root=root, skip_models=bool(skip_models))
        return out.get("steps", {}).get("learning_distillation") or {"ok": False, "error": "missing_learning_distillation"}

    return [
        LoopSpec("daily_cycle", "research", 120.0, _daily_cycle),
        LoopSpec("review_cycle", "research", 600.0, _review_cycle),
        LoopSpec("ceo_daily_review", "research", 1800.0, _ceo),
        LoopSpec("pnl_review", "research", 300.0, _pnl_review),
        LoopSpec("comparisons", "research", 300.0, _comparisons),
        LoopSpec("trade_cycle_intelligence", "research", 600.0, _trade_cycle_intel),
        LoopSpec("promotion_capital_cycle", "research", 1800.0, _promo_capital),
        LoopSpec("learning_distillation_snapshot", "research", 600.0, _learning_snapshot),
    ]


def run_role_supervisor_once(
    *,
    role: ServerRole,
    runtime_root: Optional[Path] = None,
    skip_models: bool = True,
    force_all_due: bool = False,
) -> Dict[str, Any]:
    """
    One supervisor step: runs any due loops, writes per-loop result artifacts, and updates loop status.
    """
    enforce_non_live_env_defaults()
    ok, why = _safety_assert_non_live()
    if not ok:
        return {"ok": False, "blocked": True, "reason": why, "role": role}
    root = _runtime_root(runtime_root)
    status_p = _role_status_path(root, role)
    status = _read_json(status_p)
    loops_state = status.get("loops") if isinstance(status.get("loops"), dict) else {}
    loops_state = dict(loops_state) if isinstance(loops_state, dict) else {}
    now = time.time()

    # Write role contract for visibility.
    _write_json(_os_dir(root) / "role_contract.json", role_contract())

    specs = _ops_loops() if role == "ops" else _research_loops(skip_models=bool(skip_models))
    ran: List[str] = []
    for spec in specs:
        st = loops_state.get(spec.loop_id) if isinstance(loops_state.get(spec.loop_id), dict) else {}
        last = float(st.get("last_run_unix") or 0.0) if isinstance(st, dict) else 0.0
        due = force_all_due or (now - last) >= float(spec.interval_sec)
        if not due:
            continue
        started = time.time()
        try:
            result = spec.runner(root)
            ok_loop = True
            err = None
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__}
            ok_loop = False
            err = type(exc).__name__
        finished = time.time()
        loops_state[spec.loop_id] = {
            "loop_id": spec.loop_id,
            "owner_role": spec.owner_role,
            "interval_sec": spec.interval_sec,
            "last_run_unix": finished,
            "last_duration_sec": round(finished - started, 6),
            "ok": bool(ok_loop),
            "last_error": err,
            "result_path": str(_loop_result_path(root, role, spec.loop_id)),
        }
        _write_json(_loop_result_path(root, role, spec.loop_id), {"generated_at": _iso(), "loop_id": spec.loop_id, "result": result})
        ran.append(spec.loop_id)

    payload = {
        "truth_version": "operating_system_loop_status_v1",
        "generated_at": _iso(),
        "role": role,
        "runtime_root": str(root),
        "ran": ran,
        "loops": loops_state,
        "honesty": "Supervisor is non-live; loops emit artifacts and route shadow tasks only.",
    }
    _write_json(status_p, payload)
    return {"ok": True, "role": role, "ran": ran, "status_path": str(status_p)}


