"""Canonical normalization for internal closed-trade rows — timestamps, avenue, PnL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from trading_ai.intelligence.ts_parse import parse_trade_ts


def avenue_normalized(t: Dict[str, Any]) -> str:
    for k in ("avenue", "avenue_id", "avenue_name"):
        v = t.get(k)
        if v is not None and str(v).strip():
            return str(v).strip().lower()
    return ""


def net_pnl_normalized(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def normalize_trade_row(t: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a shallow copy with canonical fields + exclusion metadata.

    Keys added:
    - _norm_ts: unix seconds or None
    - _norm_avenue: lowercased avenue or ""
    - _norm_net: float or None
    - _excluded_from_windows: bool
    - _exclusion_reasons: list[str]
    """
    out = dict(t)
    reasons: List[str] = []
    ts = parse_trade_ts(t)
    if ts is None:
        reasons.append("no_parseable_timestamp")
    av = avenue_normalized(t)
    if not av:
        reasons.append("missing_avenue_label")
    net = net_pnl_normalized(t)
    if net is None:
        reasons.append("missing_net_pnl")
    excluded = bool(reasons)
    out["_norm_ts"] = ts
    out["_norm_avenue"] = av
    out["_norm_net"] = net
    out["_excluded_from_windows"] = excluded
    out["_exclusion_reasons"] = reasons
    return out


def normalize_trade_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Normalize all rows; return (normalized_list, data_quality_summary)."""
    normed: List[Dict[str, Any]] = []
    excluded = 0
    reason_counts: Dict[str, int] = {}
    for t in rows:
        if not isinstance(t, dict):
            excluded += 1
            reason_counts["not_a_dict"] = reason_counts.get("not_a_dict", 0) + 1
            continue
        n = normalize_trade_row(t)
        normed.append(n)
        if n.get("_excluded_from_windows"):
            excluded += 1
            for r in n.get("_exclusion_reasons") or []:
                reason_counts[r] = reason_counts.get(r, 0) + 1
    dq = {
        "input_rows": len(rows),
        "normalized_rows": len(normed),
        "excluded_from_time_or_avenue_windows": excluded,
        "exclusion_reason_counts": reason_counts,
        "usable_for_windows": sum(1 for x in normed if not x.get("_excluded_from_windows")),
    }
    return normed, dq


def trades_usable_for_windows(normed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [t for t in normed if not t.get("_excluded_from_windows")]
