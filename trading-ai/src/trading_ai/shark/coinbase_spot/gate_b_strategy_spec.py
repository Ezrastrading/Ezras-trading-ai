"""Strict entry contract for Gate B gainers (staged)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence


@dataclass
class StrictEntryDecision:
    entry_pass: bool
    reasons: List[str]


def strict_entry_check(row: Mapping[str, Any], *, open_product_ids: Sequence[str]) -> StrictEntryDecision:
    _ = open_product_ids
    reasons: List[str] = []
    vol = float(row.get("volume_24h_usd") or 0.0)
    if vol < 1_000_000.0:
        reasons.append("volume_too_low")
    spread = float(row.get("spread_bps") or 0.0)
    if spread > 30.0:
        reasons.append("spread_too_wide")
    bid = float(row.get("best_bid") or 0.0)
    ask = float(row.get("best_ask") or 0.0)
    if bid <= 0 or ask <= 0 or bid >= ask:
        reasons.append("book_invalid")
    return StrictEntryDecision(entry_pass=len(reasons) == 0, reasons=reasons)


def strict_entry_dict(row: Mapping[str, Any], **kwargs: Any) -> Dict[str, Any]:
    d = strict_entry_check(row, **kwargs)
    return {"entry_pass": d.entry_pass, "reasons": d.reasons}
