"""Map raw regime strings to coarse buckets; per-regime performance rollups."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

from trading_ai.organism.types import RegimeBucket


def classify_regime_bucket(raw: str) -> str:
    """Classify a trade's regime field into TREND / RANGE / VOLATILE / LOW_LIQUIDITY / UNKNOWN."""
    s = (raw or "").strip().upper()
    if not s:
        return RegimeBucket.UNKNOWN.value
    if re.search(r"LOW[_\s-]*LIQ|ILLIQUID|THIN", s):
        return RegimeBucket.LOW_LIQUIDITY.value
    if re.search(r"VOL|HIGH[_\s-]*VOL|CHAOS", s):
        return RegimeBucket.VOLATILE.value
    if re.search(r"TREND|DRIFT|MOM", s):
        return RegimeBucket.TREND.value
    if re.search(r"RANGE|CHOP|FLAT", s):
        return RegimeBucket.RANGE.value
    if s in ("TREND", "RANGE", "VOLATILE", "LOW_LIQUIDITY"):
        return s
    return RegimeBucket.UNKNOWN.value


def trades_for_regime(trades: Sequence[Mapping[str, Any]], bucket: str) -> List[Dict[str, Any]]:
    b = bucket.upper()
    out: List[Dict[str, Any]] = []
    for t in trades:
        rb = str(t.get("regime_bucket") or classify_regime_bucket(str(t.get("regime") or "")))
        if rb.upper() == b:
            out.append(dict(t))
    return out


def performance_by_regime(trades: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Net PnL and mean PnL per trade (expectancy proxy) per regime bucket."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        rb = str(t.get("regime_bucket") or classify_regime_bucket(str(t.get("regime") or "")))
        buckets.setdefault(rb, []).append(dict(t))
    out: Dict[str, Any] = {}
    for rb, ts in buckets.items():
        pnls = [float(x.get("net_pnl") or 0.0) for x in ts]
        n = len(pnls)
        exp = (sum(pnls) / n) if n else 0.0
        out[rb] = {
            "trades": n,
            "net_pnl": sum(pnls),
            "expectancy": exp,
        }
    return out


def edge_allowed_in_regime(
    edge_regime_tags: List[str],
    current_bucket: str,
) -> bool:
    """Edges should only scale in regimes listed in tags when tags are non-empty."""
    if not edge_regime_tags:
        return True
    cur = current_bucket.upper()
    tags = {x.strip().upper() for x in edge_regime_tags if x and str(x).strip()}
    return cur in tags or RegimeBucket.UNKNOWN.value in tags
