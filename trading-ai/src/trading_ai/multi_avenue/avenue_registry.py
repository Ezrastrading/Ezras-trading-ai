"""Registry of avenues — wired, partial, disabled, future. No execution logic."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.runtime_paths import ezras_runtime_root

from trading_ai.multi_avenue.registry_overlay import load_registry_overlay


def default_avenue_definitions() -> List[Dict[str, Any]]:
    """
    Canonical avenue list. Extend when wiring new venues — keep ids stable.

    Isolation: each avenue has its own gate list; no shared mutable state.
    """
    return [
        {
            "avenue_id": "A",
            "avenue_name": "coinbase_nte",
            "display_name": "Avenue A — Coinbase NTE",
            "venue_name": "coinbase",
            "market_type": "spot_crypto",
            "wiring_status": "wired",
            "notes": "Primary NTE / Gate A execution path; validation + runtime policy proven in-repo.",
            "gates": ["gate_a"],
        },
        {
            "avenue_id": "B",
            "avenue_name": "kalshi",
            "display_name": "Avenue B — Kalshi / prediction markets",
            "venue_name": "kalshi",
            "market_type": "prediction",
            "wiring_status": "wired",
            "notes": "Shark / Gate B path; live flag + validation artifact gate production state.",
            "gates": ["gate_b"],
        },
        {
            "avenue_id": "C",
            "avenue_name": "tastytrade",
            "display_name": "Avenue C — Tastytrade",
            "venue_name": "tastytrade",
            "market_type": "brokerage_options_futures",
            "wiring_status": "scaffold_only",
            "notes": "Designated Avenue C venue (Tastytrade). Execution not wired — no live scope until independent proof.",
            "gates": [],
        },
    ]


def _asymmetric_enabled() -> bool:
    try:
        from trading_ai.asymmetric.config import load_asymmetric_config

        return bool(load_asymmetric_config().enabled)
    except Exception:
        return False


def _with_asymmetric_gates(avenues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    When enabled, expose an isolated asymmetric gate per avenue as *additional* gate ids.

    This does not wire execution; it only makes the gate visible to routing/registry layers.
    """
    if not _asymmetric_enabled():
        return avenues
    out: List[Dict[str, Any]] = []
    for a in avenues:
        row = dict(a)
        gates = list(row.get("gates") or [])
        extra: List[str] = []
        for g in gates:
            g = str(g).strip()
            if not g:
                continue
            ag = f"{g}_asymmetric"
            if ag not in gates and ag not in extra:
                extra.append(ag)
        row["gates"] = gates + extra
        out.append(row)
    return out


def merged_avenue_definitions(*, runtime_root: Path | None = None) -> List[Dict[str, Any]]:
    """
    Canonical avenue list = built-in defaults plus optional ``registry_overlay.json`` additions.

    Overlay avenues with the same ``avenue_id`` as a default row are ignored (defaults win).
    ``additional_gates`` merges extra gate ids onto existing or overlay avenues.
    """
    base_list = _with_asymmetric_gates(default_avenue_definitions())
    by_id: Dict[str, Dict[str, Any]] = {str(a["avenue_id"]): dict(a) for a in base_list}
    ov = load_registry_overlay(runtime_root=runtime_root)
    for a in ov.get("additional_avenues") or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("avenue_id") or "").strip()
        if not aid or aid in by_id:
            continue
        by_id[aid] = dict(a)
    for aid_raw, glist in (ov.get("additional_gates") or {}).items():
        aid = str(aid_raw).strip()
        if not aid or aid not in by_id:
            continue
        if not isinstance(glist, list):
            continue
        row = by_id[aid]
        gates = list(row.get("gates") or [])
        for g in glist:
            g = str(g).strip()
            if g and g not in gates:
                gates.append(g)
        row["gates"] = gates
    return list(by_id.values())


def build_avenue_registry_snapshot(*, runtime_root: Path | None = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    avenues = merged_avenue_definitions(runtime_root=root)
    return {
        "artifact": "avenue_registry_snapshot",
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "avenues": avenues,
        "auto_attach_ready_for_future_avenues": True,
        "honesty_note": "Scaffold avenues receive framework shells only — no fabricated execution.",
    }
