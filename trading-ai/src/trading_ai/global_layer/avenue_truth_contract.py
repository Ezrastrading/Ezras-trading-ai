"""
Expected avenues vs federated trade coverage — fairness and play-money labeling.

Infrastructure-only: does not assert strategy edge or profitability.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Set

# Venues that must never be mixed into USD capital conclusions without explicit labeling.
PLAY_MONEY_AVENUES: Set[str] = {"manifold", "mana"}


def expected_truth_avenues() -> Set[str]:
    """
    Which avenues the organism *expects* to see in canonical trade truth when corresponding
    execution surfaces are enabled (best-effort; not all Kalshi activity flows through databank yet).

    ``KALSHI_TRUTH_EXPECTED=false`` disables expecting Kalshi rows in federation (still logs warnings if absent).
    """
    out: Set[str] = set()
    if (os.environ.get("COINBASE_ENABLED") or "").strip().lower() in ("1", "true", "yes"):
        out.add("coinbase")
    kalshi_opt_out = (os.environ.get("KALSHI_TRUTH_EXPECTED") or "true").strip().lower() in (
        "0",
        "false",
        "no",
    )
    if not kalshi_opt_out and (os.environ.get("KALSHI_API_KEY") or "").strip():
        out.add("kalshi")
    try:
        from trading_ai.shark.avenues import load_avenues

        for av in load_avenues().values():
            if getattr(av, "status", "") == "active" and getattr(av, "platform", "") == "kalshi":
                if not kalshi_opt_out:
                    out.add("kalshi")
    except Exception:
        pass
    return out


def normalize_avenue_key(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("a", "coinbase"):
        return "coinbase"
    if s in ("b", "kalshi"):
        return "kalshi"
    if s in ("c", "tastytrade"):
        return "tastytrade"
    return s or "unknown"


def label_play_money(avenue_key: str) -> bool:
    return normalize_avenue_key(avenue_key) in PLAY_MONEY_AVENUES or "manifold" in avenue_key.lower()


def build_representation_status(
    *,
    by_avenue_counts: Dict[str, Any],
    expected: Set[str],
) -> Dict[str, Any]:
    """
    Per-avenue coverage for federation fairness.

    Each row includes legacy ``representation`` (fully_represented | partial | missing) plus explicit
    booleans ``present`` / ``partial`` / ``missing`` so downstream code never relies on silent equality.

    ``partial`` = trades exist but material fields are unknown or quality score is degraded.
    """
    status: Dict[str, Any] = {}
    for av in sorted(expected):
        row: Dict[str, Any] = {}
        if av in by_avenue_counts and isinstance(by_avenue_counts[av], dict):
            row = by_avenue_counts[av]
        n = int(row.get("trade_count") or 0) if row else 0
        if not row and av in by_avenue_counts:
            n = int(by_avenue_counts[av] or 0)

        unknown_net = int(row.get("unknown_net_count") or 0) if row else 0
        rq = float(row.get("representation_quality_score") or 100.0) if row else 100.0
        partial_quality = n > 0 and (unknown_net > 0 or rq < 88.0)

        missing_b = n == 0
        partial_b = partial_quality
        present_b = n > 0 and not partial_quality

        if missing_b:
            st = "missing"
        elif partial_b:
            st = "partial"
        else:
            st = "fully_represented"

        status[av] = {
            "representation": st,
            "present": present_b,
            "partial": partial_b,
            "missing": missing_b,
            "trade_count": n,
            "note": None
            if n > 0
            else (
                "No closed trades in federated truth — Kalshi may be active but not ingested into "
                "databank/NTE; do not infer zero activity."
                if av == "kalshi"
                else "No trades in federated layer for this expected avenue."
            ),
        }
    return status
