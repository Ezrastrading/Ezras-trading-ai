"""
Authoritative closed-trade tagging for evolution / safest / ROI — Gate A vs Gate B vs unknown.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

REQUIRED_CORE = ("trade_id", "avenue_id", "avenue_name", "strategy_id", "asset")
REQUIRED_ASYM = ("gate_family", "gate_id", "trade_type")


def validate_evolution_tags(row: Mapping[str, Any]) -> List[str]:
    """Return validation errors for evolution-safe tagging (empty = ok)."""
    errs: List[str] = []
    for k in REQUIRED_CORE:
        if not str(row.get(k) or "").strip():
            errs.append(f"missing:{k}")
    # Legacy: trading_gate (gate_a/gate_b) still supported, but newer paths should prefer
    # gate_family + gate_id (A_CORE/B_ASYM etc.) for explicit separation.
    tg = row.get("trading_gate")
    if tg is not None and str(tg).strip():
        if str(tg).strip().lower() not in ("gate_a", "gate_b", "unknown"):
            errs.append("invalid:trading_gate")
    gf = str(row.get("gate_family") or "").strip().lower()
    if gf:
        if gf not in ("core", "asymmetric"):
            errs.append("invalid:gate_family")
        for k in REQUIRED_ASYM:
            if not str(row.get(k) or "").strip():
                errs.append(f"missing:{k}")
        gid = str(row.get("gate_id") or "").strip()
        if gid and gid not in ("A_CORE", "A_ASYM", "B_CORE", "B_ASYM", "C_CORE", "C_ASYM"):
            errs.append("invalid:gate_id")
    aid = str(row.get("avenue_id") or "").upper()
    if aid == "A" and row.get("trading_gate") is None:
        errs.append("recommend:trading_gate_for_avenue_A")
    return errs


def infer_trading_gate(row: Mapping[str, Any]) -> str:
    """Best-effort gate label for analytics."""
    # New style: explicit asym/core; map to gate_a/gate_b/unknown for legacy dashboards.
    gf = str(row.get("gate_family") or "").strip().lower()
    gid = str(row.get("gate_id") or "").strip().upper()
    if gf and gid:
        if gid.startswith("A_"):
            return "gate_a"
        if gid.startswith("B_"):
            return "gate_b"
        return "unknown"
    tg = row.get("trading_gate")
    if tg is not None and str(tg).strip():
        return str(tg).strip().lower()
    sid = str(row.get("strategy_id") or "").lower()
    lane = str(row.get("edge_lane") or "").lower()
    if "gate_b" in sid or "gainer" in sid or "momentum" in lane:
        return "gate_b"
    return "gate_a"
