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
from typing import Any, Dict, Literal, Optional, Tuple

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

