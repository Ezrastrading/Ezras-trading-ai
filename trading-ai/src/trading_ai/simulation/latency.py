"""Deterministic latency / jitter model for simulated market and matching paths."""

from __future__ import annotations

from typing import Any, Dict


def sample_latency_bundle(*, tick_index: int, venue: str = "sim") -> Dict[str, Any]:
    """
    Return inbound/outbound latency ms and jitter labels without randomness (seed = tick_index).

    Uses a simple mixed congruential pattern so tests are stable.
    """
    t = max(0, int(tick_index))
    base = 8 + (t % 23)
    jitter = (t * 17) % 41
    inbound_ms = float(base + jitter)
    outbound_ms = float(base + (jitter // 2))
    return {
        "truth_version": "sim_latency_bundle_v1",
        "venue": venue,
        "tick_index": t,
        "inbound_ms": round(inbound_ms, 3),
        "outbound_ms": round(outbound_ms, 3),
        "jitter_class": "high" if jitter > 30 else ("medium" if jitter > 15 else "low"),
        "honesty": "Synthetic latency only; not measured from a real venue.",
    }


def apply_slippage_bps(*, tick_index: int, side: str = "buy") -> float:
    """Slippage in basis points (always non-negative magnitude for sim bookkeeping)."""
    t = max(0, int(tick_index))
    mag = 1.0 + float((t * 13) % 17)  # 1..17 bps
    if str(side).lower() in ("sell", "short"):
        return -mag
    return mag
