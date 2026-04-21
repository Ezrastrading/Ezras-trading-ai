"""Explicit read APIs for Gate A / Gate B — no silent universal overwrite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.ratios.universal_ratio_registry import build_universal_ratio_policy_bundle


def gate_a_ratio_view(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    b = build_universal_ratio_policy_bundle()
    g = b.gate_overlays.get("gate_a") or {}
    u = b.universal_ratios
    coin = b.avenue_overlays.get("coinbase") or {}
    return {
        "gate": "A",
        "direct_deployable_note": "Read deployable_capital_report.json + quote balances",
        "route_conservative_note": "Single-leg validation path; multi-leg search is diagnostic only",
        "reserve_policy": {
            "hard_reserve_ratio": (u.get("universal.hard_reserve_ratio") or {}).get("value"),
            "soft_reserve_ratio": (u.get("universal.soft_reserve_ratio") or {}).get("value"),
        },
        "per_trade_cap_fraction": (g.get("gate.gate_a.per_trade_cap_fraction") or {}).get("value"),
        "trailing_ratio": (g.get("gate.gate_a.trailing_ratio_ref") or {}).get("value"),
        "avenue_coinbase_reserve_buffer": (coin.get("avenue.coinbase.reserve_buffer_ratio") or {}).get("value"),
        "universal_stops_targets": {
            "tp_min": (u.get("universal.profit_target_min_ratio") or {}).get("value"),
            "sl_max": (u.get("universal.stop_loss_max_ratio") or {}).get("value"),
        },
        "source": "trading_ai.ratios.gate_ratio_access",
    }


def gate_b_ratio_view(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    b = build_universal_ratio_policy_bundle()
    g = b.gate_overlays.get("gate_b") or {}
    u = b.universal_ratios
    return {
        "gate": "B",
        "momentum_safe_deployable_fraction": (g.get("gate.gate_b.momentum_safe_deployable_fraction") or {}).get(
            "value"
        ),
        "reserve_policy": {
            "hard_reserve_ratio": (u.get("universal.hard_reserve_ratio") or {}).get("value"),
            "soft_reserve_ratio": (u.get("universal.soft_reserve_ratio") or {}).get("value"),
        },
        "concentration_cap": (u.get("universal.max_concurrent_exposure_ratio") or {}).get("value"),
        "confidence_multiplier_placeholder": (b.adaptive_multipliers.get("adaptive.normal.size_multiplier") or {}).get(
            "value"
        ),
        "source": "trading_ai.ratios.gate_ratio_access",
    }
