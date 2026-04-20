"""
Universal live-order safety contract — registry of venue/avenue/gate wiring + fail-closed evaluation.

Wired from :func:`trading_ai.nte.hardening.live_order_guard.assert_live_order_permitted` (Coinbase) and
:func:`trading_ai.shark.execution_live._submit_order_impl` (non-Coinbase shark outlets).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter

WiringClass = Literal["fully_wired", "partially_wired", "legacy_guarded_only"]

_COVERAGE: Dict[str, Dict[str, Any]] = {
    "coinbase|gate_a": {
        "avenue_name": "coinbase",
        "gate": "gate_a",
        "wiring_class": "fully_wired",
        "scope": "avenue_a_coinbase_gate_a",
        "notes": "Coinbase spot Gate A — live_order_guard + failsafe.",
    },
    "coinbase|gate_b": {
        "avenue_name": "coinbase",
        "gate": "gate_b",
        "wiring_class": "fully_wired",
        "scope": "avenue_a_coinbase_gate_b",
        "notes": "Coinbase spot Gate B — live_order_guard + failsafe.",
    },
    "kalshi|gate_b": {
        "avenue_name": "kalshi",
        "gate": "gate_b",
        "wiring_class": "partially_wired",
        "scope": "kalshi_gate_b_execution_live",
        "notes": "Kalshi Gate B path via execution_live.submit_order + existing guards.",
    },
    "kalshi|default": {
        "avenue_name": "kalshi",
        "gate": "default",
        "wiring_class": "partially_wired",
        "scope": "kalshi_default_bucket",
        "notes": "Fallback bucket for Kalshi when gate not tagged gate_b.",
    },
    "robinhood|default": {
        "avenue_name": "robinhood",
        "gate": "default",
        "wiring_class": "partially_wired",
        "scope": "shark_robinhood",
        "notes": "Shark outlet — register for promotion when proofs exist.",
    },
    "tastytrade|default": {
        "avenue_name": "tastytrade",
        "gate": "default",
        "wiring_class": "partially_wired",
        "scope": "shark_tastytrade",
        "notes": "Shark outlet — register for promotion when proofs exist.",
    },
    "manifold|default": {
        "avenue_name": "manifold",
        "gate": "default",
        "wiring_class": "partially_wired",
        "scope": "shark_manifold",
        "notes": "Manifold real-money path when enabled.",
    },
}

_EVAL_LOCK = threading.Lock()
_EVAL_COUNT = 0
_LAST_SHORT_CIRCUIT_KEY: Optional[str] = None
_LAST_SHORT_CIRCUIT_MONO: float = 0.0
_LAST_SHORT_CIRCUIT_ART: Optional[Dict[str, Any]] = None


def reset_universal_live_guard_metrics_for_tests() -> None:
    global _EVAL_COUNT, _LAST_SHORT_CIRCUIT_KEY, _LAST_SHORT_CIRCUIT_MONO, _LAST_SHORT_CIRCUIT_ART
    with _EVAL_LOCK:
        _EVAL_COUNT = 0
        _LAST_SHORT_CIRCUIT_KEY = None
        _LAST_SHORT_CIRCUIT_MONO = 0.0
        _LAST_SHORT_CIRCUIT_ART = None


def evaluation_count_for_tests() -> int:
    return _EVAL_COUNT


def register_universal_live_guard_entry(
    key: str,
    *,
    avenue_name: str,
    gate: str,
    wiring_class: WiringClass,
    notes: str = "",
) -> None:
    """Explicit registration for new avenues/gates (additive)."""
    _COVERAGE[key] = {
        "avenue_name": avenue_name,
        "gate": gate,
        "wiring_class": wiring_class,
        "notes": notes,
    }


def _key(avenue: str, gate: str) -> str:
    a = str(avenue or "").strip().lower()
    g = str(gate or "").strip().lower() or "default"
    return f"{a}|{g}"


def describe_universal_live_coverage() -> Dict[str, Any]:
    return {"truth_version": "universal_live_guard_registry_v1", "entries": dict(_COVERAGE)}


def evaluate_universal_live_guard(
    avenue: str,
    gate: str,
    *,
    fail_closed: bool = True,
    runtime_root: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns ``(allowed, reason, detail)``.

    When ``fail_closed`` is True (default), unknown avenue/gate keys are denied.
    """
    k = _key(avenue, gate)
    row = _COVERAGE.get(k)
    if row is None:
        row = _COVERAGE.get(_key(avenue, "default"))
    hit = row is not None
    detail: Dict[str, Any] = {
        "lookup_key": k,
        "universal_live_guard_registry_hit": hit,
        "registry_hit": hit,
        "wiring_class": (row or {}).get("wiring_class"),
        "universal_live_guard_scope": (row or {}).get("scope"),
        "notes": (row or {}).get("notes"),
    }
    if row is None:
        msg = f"universal_live_guard_unregistered:{k}"
        return (not fail_closed), msg, detail
    if row.get("wiring_class") == "legacy_guarded_only" and fail_closed:
        return False, "universal_live_guard_legacy_only_blocked_by_policy", detail
    return True, "ok", detail


def _normalize_registry_gate(execution_gate: str) -> str:
    g = str(execution_gate or "").strip().lower()
    if g in ("gate_b", "b", "gb"):
        return "gate_b"
    if g in ("gate_a", "a", "ga"):
        return "gate_a"
    if "gate_b" in g:
        return "gate_b"
    return "gate_a"


def shark_outlet_to_registry_avenue_gate(outlet: str) -> Tuple[str, str]:
    """Map execution_live outlet string to (avenue, gate) for registry lookup."""
    o = str(outlet or "").strip().lower()
    if o == "kalshi":
        return "kalshi", "gate_b"
    if o == "coinbase":
        return "coinbase", "gate_a"
    return o, "default"


def run_universal_live_guard_precheck(
    avenue: str,
    gate: str,
    *,
    runtime_root: Optional[Path] = None,
    trade_id: Optional[str] = None,
    check_execution_halt: bool = True,
    fail_closed: bool = True,
) -> Dict[str, Any]:
    """
    Single evaluation per live attempt: registry + optional execution halt.

    Persists last eval to ``data/control/universal_live_guard_last_eval.json``.
    Short-circuits duplicate identical (avenue, gate, trade_id) within 2ms (same stack double-call guard).
    """
    global _EVAL_COUNT, _LAST_SHORT_CIRCUIT_KEY, _LAST_SHORT_CIRCUIT_MONO, _LAST_SHORT_CIRCUIT_ART
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    av = str(avenue or "").strip().lower()
    g = _normalize_registry_gate(gate)
    sig = f"{av}|{g}|{trade_id or ''}"
    now_m = time.monotonic()
    with _EVAL_LOCK:
        if (
            _LAST_SHORT_CIRCUIT_KEY == sig
            and _LAST_SHORT_CIRCUIT_ART is not None
            and (now_m - _LAST_SHORT_CIRCUIT_MONO) < 0.002
        ):
            dup = dict(_LAST_SHORT_CIRCUIT_ART)
            dup["universal_live_guard_duplicate_short_circuit"] = True
            return dup
        _EVAL_COUNT += 1

    allowed, reason, det = evaluate_universal_live_guard(av, g, fail_closed=fail_closed, runtime_root=root)
    reason_codes: List[str] = []
    if not allowed:
        reason_codes.append(reason)
    halt_blocked = False
    if check_execution_halt:
        try:
            from trading_ai.safety.kill_switch_engine import evaluate_execution_block

            blocked, hr = evaluate_execution_block(runtime_root=root)
            if blocked:
                halt_blocked = True
                reason_codes.append(str(hr))
                allowed = False
        except Exception:
            reason_codes.append("execution_halt_check_error")

    artifact: Dict[str, Any] = {
        "truth_version": "universal_live_guard_eval_v1",
        "universal_live_guard_evaluated": True,
        "universal_live_guard_allowed": bool(allowed and not halt_blocked),
        "universal_live_guard_reason_codes": reason_codes,
        "universal_live_guard_registry_hit": bool(det.get("registry_hit")),
        "universal_live_guard_scope": det.get("universal_live_guard_scope"),
        "lookup_key": det.get("lookup_key"),
        "wiring_class": det.get("wiring_class"),
        "runtime_root": str(root),
        "trade_id": trade_id,
        "halt_execution_block_active": halt_blocked,
        "honesty": "Registry pass does not remove failsafe or venue guards — ordered before failsafe in live_order_guard.",
    }
    with _EVAL_LOCK:
        _LAST_SHORT_CIRCUIT_KEY = sig
        _LAST_SHORT_CIRCUIT_MONO = time.monotonic()
        _LAST_SHORT_CIRCUIT_ART = dict(artifact)

    try:
        ad = LocalStorageAdapter(runtime_root=root)
        ad.write_json("data/control/universal_live_guard_last_eval.json", artifact)
    except OSError:
        pass

    if not artifact["universal_live_guard_allowed"]:
        primary = reason_codes[0] if reason_codes else "universal_live_guard_blocked"
        raise RuntimeError(primary)

    return artifact


def write_universal_live_guard_truth(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    payload = {
        **describe_universal_live_coverage(),
        "runtime_root": str(root),
        "honesty": "Registry describes declared wiring — absence of an entry is a blocker when fail_closed is used.",
    }
    ad.write_json("data/control/universal_live_guard_truth.json", payload)
    return {"ok": True, "path": str(root / "data" / "control" / "universal_live_guard_truth.json"), "payload": payload}
