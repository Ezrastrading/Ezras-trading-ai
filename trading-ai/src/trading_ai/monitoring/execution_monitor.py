"""Per-trade execution quality: fill prices, slippage, latency, degradation detection."""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Literal, Optional

from trading_ai.nte.paths import nte_memory_dir

logger = logging.getLogger(__name__)

MitigationAction = Literal["none", "reduce_size", "block_strategy"]


@dataclass
class Mitigation:
    action: MitigationAction
    size_factor: float = 1.0
    reason_codes: List[str] = field(default_factory=list)


@dataclass
class ExecutionSample:
    trade_id: str
    expected_fill_price: float
    actual_fill_price: float
    slippage: float
    latency_ms: float
    ts: float = field(default_factory=time.time)
    side: str = "buy"  # buy|sell — affects signed slippage interpretation only in logs

    def to_jsonl_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["slippage_bps"] = _slippage_bps(self.expected_fill_price, self.actual_fill_price, self.side)
        return d


def _slippage_bps(expected: float, actual: float, side: str) -> float:
    if not expected or expected != expected:  # NaN
        return 0.0
    s = (side or "buy").strip().lower()
    if s == "sell":
        raw = (expected - actual) / expected * 10_000.0
    else:
        raw = (actual - expected) / expected * 10_000.0
    return float(raw)


def execution_metrics_path() -> Path:
    p = nte_memory_dir() / "execution_metrics.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _slippage_threshold_exceeded(sample: ExecutionSample) -> bool:
    bps_raw = (os.environ.get("EXECUTION_MONITOR_SLIPPAGE_THRESHOLD_BPS") or "").strip()
    abs_raw = (os.environ.get("EXECUTION_MONITOR_SLIPPAGE_THRESHOLD_ABS") or "").strip()
    bps_thr: Optional[float] = None
    abs_thr: Optional[float] = None
    if bps_raw:
        try:
            bps_thr = abs(float(bps_raw))
        except ValueError:
            pass
    if abs_raw:
        try:
            abs_thr = abs(float(abs_raw))
        except ValueError:
            pass
    bps = abs(_slippage_bps(sample.expected_fill_price, sample.actual_fill_price, sample.side))
    price_slip = abs(sample.actual_fill_price - sample.expected_fill_price)
    if bps_thr is not None and bps > bps_thr:
        return True
    if abs_thr is not None and price_slip > abs_thr:
        return True
    return False


def _latency_baseline_ms(history: Deque[float]) -> float:
    env_b = (os.environ.get("EXECUTION_MONITOR_LATENCY_BASELINE_MS") or "").strip()
    if env_b:
        try:
            return max(1.0, float(env_b))
        except ValueError:
            pass
    if len(history) < 2:
        return 100.0
    return max(1.0, float(statistics.median(history)))


def detect_degradation(
    sample: ExecutionSample,
    *,
    latency_history: Optional[Deque[float]] = None,
) -> Dict[str, Any]:
    """
    Returns flags when latency > 2× baseline or slippage exceeds threshold.

    ``latency_history`` should hold recent *completed* latencies (ms), oldest last;
    current sample latency is compared to baseline from history (not including this sample).
    """
    hist = latency_history
    if hist is None:
        hist = deque()
    baseline = _latency_baseline_ms(hist)
    latency_bad = sample.latency_ms > 2.0 * baseline
    slippage_bad = _slippage_threshold_exceeded(sample)
    flagged = bool(latency_bad or slippage_bad)
    return {
        "flagged": flagged,
        "latency_bad": latency_bad,
        "slippage_bad": slippage_bad,
        "latency_ms": sample.latency_ms,
        "baseline_latency_ms": baseline,
        "slippage_bps": _slippage_bps(
            sample.expected_fill_price, sample.actual_fill_price, sample.side
        ),
    }


def _mitigation_for_flags(flags: Dict[str, Any]) -> Mitigation:
    if not flags.get("flagged"):
        return Mitigation(action="none", size_factor=1.0, reason_codes=[])
    action = (os.environ.get("EXECUTION_DEGRADATION_ACTION") or "reduce_size").strip().lower()
    reasons: List[str] = []
    if flags.get("latency_bad"):
        reasons.append("latency_gt_2x_baseline")
    if flags.get("slippage_bad"):
        reasons.append("slippage_gt_threshold")
    if action == "block_strategy":
        return Mitigation(action="block_strategy", size_factor=0.0, reason_codes=reasons)
    # reduce_size (default)
    try:
        factor = float((os.environ.get("EXECUTION_DEGRADATION_SIZE_FACTOR") or "0.5").strip())
    except ValueError:
        factor = 0.5
    factor = max(0.0, min(1.0, factor))
    return Mitigation(action="reduce_size", size_factor=factor, reason_codes=reasons)


def record_execution(
    sample: ExecutionSample,
    *,
    latency_history: Optional[Deque[float]] = None,
    append_metrics: bool = True,
) -> Dict[str, Any]:
    """
    Persist one line to ``execution_metrics.jsonl`` and return degradation + mitigation.
    Updates ``latency_history`` with this sample's latency when provided.
    """
    flags = detect_degradation(sample, latency_history=latency_history)
    mit = _mitigation_for_flags(flags)
    line = {
        **sample.to_jsonl_dict(),
        "degradation": flags,
        "mitigation": asdict(mit),
    }
    if append_metrics:
        p = execution_metrics_path()
        try:
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, default=str) + "\n")
        except Exception as exc:
            logger.warning("execution_metrics append failed: %s", exc)
    if latency_history is not None:
        latency_history.append(sample.latency_ms)
        max_n = int((os.environ.get("EXECUTION_MONITOR_LATENCY_WINDOW") or "64").strip() or "64")
        max_n = max(4, min(512, max_n))
        while len(latency_history) > max_n:
            latency_history.popleft()
    return {"flags": flags, "mitigation": mit, "line": line}


class ExecutionMonitor:
    """Stateful monitor with rolling latency window for repeated ``record_execution`` calls."""

    def __init__(self) -> None:
        self._latencies: Deque[float] = deque()

    def record(
        self,
        trade_id: str,
        expected_fill_price: float,
        actual_fill_price: float,
        latency_ms: float,
        *,
        side: str = "buy",
    ) -> Dict[str, Any]:
        slip = actual_fill_price - expected_fill_price
        sample = ExecutionSample(
            trade_id=trade_id,
            expected_fill_price=expected_fill_price,
            actual_fill_price=actual_fill_price,
            slippage=slip,
            latency_ms=latency_ms,
            side=side,
        )
        return record_execution(sample, latency_history=self._latencies, append_metrics=True)
