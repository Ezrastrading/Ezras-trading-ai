"""
Global-layer entrypoint for opportunity pressure (delegates to org_organism implementation).

Bots and loops should prefer importing from here for a stable ``global_layer`` surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from trading_ai.org_organism.opportunity_pressure import build_opportunity_pressure_bundle as _build_bundle

__all__ = ["build_opportunity_pressure_bundle", "rank_markets_by_pressure"]


def build_opportunity_pressure_bundle(*, runtime_root: Path) -> Dict[str, Any]:
    return _build_bundle(runtime_root=Path(runtime_root).resolve())


def rank_markets_by_pressure(*, runtime_root: Path) -> Dict[str, Any]:
    """Return bundle plus any explicit ranking list exposed by the snapshot."""
    bundle = build_opportunity_pressure_bundle(runtime_root=Path(runtime_root).resolve())
    snap = bundle.get("opportunity_pressure_snapshot") or {}
    ranked = snap.get("ranked_avenues") or snap.get("avenues_ranked") or []
    return {"bundle": bundle, "ranked": ranked}
