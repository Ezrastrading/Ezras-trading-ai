"""
Global execution lock under ``data/control/system_execution_lock.json``.

All live order paths must consult :func:`require_live_execution_allowed` before submitting.
Hard preflight (:func:`validate_nt_entry_hard_guard`) enforces coherent product + capital truth.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)

DEFAULT_LOCK: Dict[str, Any] = {
    "system_locked": True,
    "ready_for_live_execution": True,
    "validated_by": "micro_validation_passed",
    "gate_a_enabled": True,
    "gate_b_enabled": False,
    "last_validation_timestamp": None,
    "safety_checks": {
        "policy_aligned": True,
        "capital_truth_valid": True,
        "artifacts_writing": True,
        "supabase_connected": True,
    },
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_system_execution_lock_file(*, runtime_root: Optional[Path] = None) -> Path:
    """Create default lock file if missing; does not overwrite existing."""
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    adapter = LocalStorageAdapter(runtime_root=runtime_root)
    rel = "data/control/system_execution_lock.json"
    if adapter.exists(rel):
        return adapter.root() / rel
    adapter.write_json(rel, dict(DEFAULT_LOCK))
    return adapter.root() / rel


def load_system_execution_lock(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    adapter = LocalStorageAdapter(runtime_root=runtime_root)
    ensure_system_execution_lock_file(runtime_root=runtime_root)
    raw = adapter.read_json("data/control/system_execution_lock.json")
    if not raw:
        return dict(DEFAULT_LOCK)
    merged = dict(DEFAULT_LOCK)
    merged.update(raw)
    if isinstance(raw.get("safety_checks"), dict):
        sc = dict(DEFAULT_LOCK["safety_checks"])
        sc.update(raw["safety_checks"])
        merged["safety_checks"] = sc
    return merged


def save_system_execution_lock(payload: Dict[str, Any], *, runtime_root: Optional[Path] = None) -> None:
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    adapter = LocalStorageAdapter(runtime_root=runtime_root)
    adapter.write_json("data/control/system_execution_lock.json", payload)


def touch_last_validation_timestamp(*, runtime_root: Optional[Path] = None) -> None:
    lock = load_system_execution_lock(runtime_root=runtime_root)
    lock["last_validation_timestamp"] = _iso_now()
    save_system_execution_lock(lock, runtime_root=runtime_root)


def require_live_execution_allowed(
    gate: Literal["gate_a", "gate_b"],
    *,
    runtime_root: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Returns (allowed, reason). Fail-closed if lock missing/invalid or gate disabled.

    ``system_locked`` must be True (operator has committed the locked configuration).
    """
    try:
        lock = load_system_execution_lock(runtime_root=runtime_root)
    except Exception as exc:
        return False, f"lock_load_failed:{exc}"

    if not bool(lock.get("system_locked")):
        return False, "system_unlocked_operator_disabled_live_execution"

    if not bool(lock.get("ready_for_live_execution")):
        return False, "ready_for_live_execution_false"

    if gate == "gate_a" and not bool(lock.get("gate_a_enabled")):
        return False, "gate_a_disabled"

    if gate == "gate_b" and not bool(lock.get("gate_b_enabled")):
        return False, "gate_b_disabled"

    sc = lock.get("safety_checks") if isinstance(lock.get("safety_checks"), dict) else {}
    for k in ("policy_aligned", "capital_truth_valid", "artifacts_writing", "supabase_connected"):
        if sc.get(k) is False:
            return False, f"safety_check_failed:{k}"

    return True, "ok"


@dataclass
class HardGuardResult:
    ok: bool
    reason: str
    chosen_product_id: Optional[str] = None
    diagnostics: Optional[Dict[str, Any]] = None


_HARD_GUARD_CACHE: Dict[str, Tuple[float, HardGuardResult]] = {}


def assert_hard_execution_guard(
    *,
    chosen_product_id: Optional[str],
    error_code: Optional[str],
    quote_sufficient: bool,
    runtime_allow: bool,
) -> Optional[str]:
    """
    Returns None if all checks pass, else a single blocking reason string.
    """
    if not (chosen_product_id or "").strip():
        return "chosen_product_id_required"
    if error_code is not None and str(error_code).strip():
        return f"error_code:{error_code}"
    if not quote_sufficient:
        return "quote_sufficient_false"
    if not runtime_allow:
        return "runtime_allow_false"
    return None


def validate_nt_entry_hard_guard(
    client: Any,
    *,
    product_id: str,
    quote_notional_usd: float,
    runtime_root: Optional[Path] = None,
) -> HardGuardResult:
    """
    Coherent validation resolution for the proposed notional + product — must succeed before NTE entry orders.
    Results are cached briefly to avoid excessive Coinbase/policy API work on every engine tick.
    """
    ttl = float((os.environ.get("NTE_HARD_GUARD_COHERENT_TTL_SEC") or "60").strip() or "60")
    bucket = round(float(quote_notional_usd), 2)
    cache_key = f"{str(product_id).strip().upper()}:{bucket}:{runtime_root or ''}"
    now = time.monotonic()
    ent = _HARD_GUARD_CACHE.get(cache_key)
    if ent and (now - ent[0]) < ttl:
        return ent[1]

    from trading_ai.nte.execution.routing.integration.validation_resolve import resolve_validation_product_coherent

    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    vr = resolve_validation_product_coherent(
        client,
        quote_notional=float(quote_notional_usd),
        preferred_product_id=str(product_id or "").strip() or "BTC-USD",
        include_policy_snapshot=False,
        write_control_artifacts=False,
        runtime_root=root,
    )
    diag = vr.diagnostics if isinstance(vr.diagnostics, dict) else {}
    chosen = (vr.chosen_product_id or "").strip()

    if vr.resolution_status != "success" or not chosen:
        ec = vr.error_code or "resolution_blocked"
        out = HardGuardResult(
            ok=False,
            reason=str(ec),
            chosen_product_id=chosen or None,
            diagnostics=diag,
        )
        _HARD_GUARD_CACHE[cache_key] = (now, out)
        return out

    if chosen.upper() != str(product_id).strip().upper():
        out = HardGuardResult(
            ok=False,
            reason=f"coherent_product_mismatch:router={product_id}:coherent={chosen}",
            chosen_product_id=chosen,
            diagnostics=diag,
        )
        _HARD_GUARD_CACHE[cache_key] = (now, out)
        return out

    block = assert_hard_execution_guard(
        chosen_product_id=chosen,
        error_code=vr.error_code,
        quote_sufficient=True,
        runtime_allow=True,
    )
    if block:
        out = HardGuardResult(ok=False, reason=block, chosen_product_id=chosen, diagnostics=diag)
        _HARD_GUARD_CACHE[cache_key] = (now, out)
        return out
    out = HardGuardResult(ok=True, reason="ok", chosen_product_id=chosen, diagnostics=diag)
    _HARD_GUARD_CACHE[cache_key] = (now, out)
    return out


def record_lock_json_dict() -> Dict[str, Any]:
    """Snapshot for logging (no secrets)."""
    try:
        return {"system_execution_lock": load_system_execution_lock()}
    except Exception as exc:
        return {"system_execution_lock_error": str(exc)}
