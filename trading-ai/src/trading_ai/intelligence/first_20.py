"""Controlled deployment: smaller size until enough live history exists."""

from typing import Optional, Tuple


def adjust_for_first_20(total_trades: int) -> Tuple[float, Optional[str]]:
    if total_trades < 20:
        return 0.25, "first_20_mode"
    return 1.0, None
