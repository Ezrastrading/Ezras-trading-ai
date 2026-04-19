"""Lightweight dict validation for NTE state blobs."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def require_keys(d: Dict[str, Any], keys: List[str]) -> Tuple[bool, List[str]]:
    missing = [k for k in keys if k not in d]
    return len(missing) == 0, missing


def validate_positive(name: str, value: Any) -> Tuple[bool, str]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, f"{name} not numeric"
    if v < 0:
        return False, f"{name} negative"
    return True, ""


def validate_range(name: str, value: Any, low: float, high: float) -> Tuple[bool, str]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, f"{name} not numeric"
    if v < low or v > high:
        return False, f"{name} out of range [{low}, {high}]"
    return True, ""
