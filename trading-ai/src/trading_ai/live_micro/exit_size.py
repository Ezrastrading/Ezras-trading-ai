from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple


def _d(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def normalize_exit_base_size(
    *,
    base_qty: float,
    base_increment: Optional[Any],
    base_min_size: Optional[Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Snap DOWN to the venue-required base_increment; enforce base_min_size.
    Returns (normalized_str or None, diagnostics).
    """
    raw = _d(base_qty)
    inc = _d(base_increment)
    min_b = _d(base_min_size)
    diag: Dict[str, Any] = {
        "original_base_qty": float(raw),
        "base_increment": str(base_increment) if base_increment is not None else None,
        "base_min_size": str(base_min_size) if base_min_size is not None else None,
    }
    if raw <= 0:
        return None, {**diag, "reason": "non_positive_base_qty"}
    if inc <= 0:
        return None, {**diag, "reason": "missing_or_invalid_base_increment"}

    steps = (raw / inc).quantize(Decimal("1"), rounding=ROUND_DOWN)
    snapped = steps * inc
    diag["snapped_base_qty"] = float(snapped)
    if snapped <= 0:
        return None, {**diag, "reason": "snapped_to_zero"}
    if min_b > 0 and snapped + (inc * Decimal("1e-9")) < min_b:
        return None, {**diag, "reason": "below_base_min_after_snap"}

    s = format(snapped, "f")
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return s, {**diag, "reason": "ok"}

