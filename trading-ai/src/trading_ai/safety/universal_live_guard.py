"""
Universal live-order safety contract — registry of venue/avenue/gate wiring + fail-closed evaluation.

This does not replace venue-specific failsafe logic; it requires every live path to declare coverage.
"""

from __future__ import annotations

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
        "notes": "Coinbase spot + Gate A — failsafe_guard + system execution lock + avenue proofs.",
    },
    "coinbase|gate_b": {
        "avenue_name": "coinbase",
        "gate": "gate_b",
        "wiring_class": "fully_wired",
        "notes": "Coinbase spot + Gate B — failsafe_guard + Gate B truth artifacts.",
    },
    "kalshi|default": {
        "avenue_name": "kalshi",
        "gate": "default",
        "wiring_class": "partially_wired",
        "notes": "Shark execution uses kill_switch + system_guard; register explicitly when promoting.",
    },
}


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

    When ``fail_closed`` is True (default), unknown avenue/gate keys are denied for *contract* checks.
    Operators may still run legacy paths that have not registered — this gate is for declared live routing.
    """
    k = _key(avenue, gate)
    row = _COVERAGE.get(k)
    if row is None:
        # Allow loose match on kalshi default bucket
        row = _COVERAGE.get(_key(avenue, "default"))
    detail = {
        "lookup_key": k,
        "registry_hit": row is not None,
        "wiring_class": (row or {}).get("wiring_class"),
        "notes": (row or {}).get("notes"),
    }
    if row is None:
        msg = f"universal_live_guard_unregistered:{k}"
        return (not fail_closed), msg, detail
    if row.get("wiring_class") == "legacy_guarded_only" and fail_closed:
        return False, "universal_live_guard_legacy_only_blocked_by_policy", detail
    return True, "ok", detail


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
