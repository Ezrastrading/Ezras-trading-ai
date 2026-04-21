"""Explicit optimization goals — weights configurable via env JSON path (deterministic)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class ObjectiveWeights:
    profitability: float
    drawdown_reduction: float
    execution_quality: float
    speed: float
    signal_quality: float
    cost_efficiency: float
    stability: float
    explainability: float


def default_weights() -> ObjectiveWeights:
    raw = (os.environ.get("EZRAS_BOT_OBJECTIVE_WEIGHTS_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_file():
            d: Dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
            return ObjectiveWeights(
                profitability=float(d.get("profitability", 1.0)),
                drawdown_reduction=float(d.get("drawdown_reduction", 1.0)),
                execution_quality=float(d.get("execution_quality", 0.5)),
                speed=float(d.get("speed", 0.25)),
                signal_quality=float(d.get("signal_quality", 1.0)),
                cost_efficiency=float(d.get("cost_efficiency", 1.0)),
                stability=float(d.get("stability", 1.0)),
                explainability=float(d.get("explainability", 0.5)),
            )
    return ObjectiveWeights(
        profitability=1.0,
        drawdown_reduction=1.0,
        execution_quality=0.5,
        speed=0.25,
        signal_quality=1.0,
        cost_efficiency=1.0,
        stability=1.0,
        explainability=0.5,
    )
