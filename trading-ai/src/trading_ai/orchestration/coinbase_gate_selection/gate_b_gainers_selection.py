"""
Gate B gainers-oriented selection — separate artifact from Gate A; does not reduce opportunity count.

Deterministic ranking from public tickers + policy; optional momentum from control artifact.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.hardening.coinbase_product_policy import ordered_validation_candidates
from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority
from trading_ai.storage.storage_adapter import LocalStorageAdapter

_DEFAULT_REL = "data/control/gate_b_product_selection_policy.json"
_SNAPSHOT = "data/control/gate_b_selection_snapshot.json"


def _default_policy() -> Dict[str, Any]:
    return {
        "truth_version": "gate_b_product_selection_policy_v1",
        "min_momentum_score": 0.0,
        "min_liquidity_proxy_usd": 0.0,
        "max_spread_bps": 80,
        "max_ticker_age_sec": 120.0,
        "max_concurrent_gainer_pursuit_per_symbol": 1,
        "cooldown_sec_after_failed_chase": 0,
        "priority_universe": "gainers_from_ordered_validation_candidates",
    }


def load_gate_b_policy(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    p = root / _DEFAULT_REL
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    return _default_policy()


def _normalize_coinbase_ticker_bid_ask(j: Dict[str, Any]) -> Dict[str, Any]:
    """Advanced Trade tickers use ``best_bid`` / ``best_ask``; align to ``bid`` / ``ask`` for one code path."""
    out = dict(j)
    if out.get("bid") is None and out.get("best_bid") is not None:
        out["bid"] = out.get("best_bid")
    if out.get("ask") is None and out.get("best_ask") is not None:
        out["ask"] = out.get("best_ask")
    return out


def _price_freshness_from_ticker(j: Dict[str, Any]) -> tuple[str, Optional[float]]:
    """Best-effort age in seconds; Coinbase ticker shapes vary."""
    now = time.time()
    for key in ("time", "last_trade_time"):
        raw = j.get(key)
        if raw is None:
            continue
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            age = max(0.0, now - ts)
            return "quote_time_present", age
        except (TypeError, ValueError):
            if isinstance(raw, str) and raw:
                try:
                    # ISO8601 from Coinbase (e.g. 2025-04-20T12:00:00Z)
                    iso = raw.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(iso)
                    ts = dt.timestamp()
                    age = max(0.0, now - ts)
                    return "quote_time_present", age
                except (TypeError, ValueError, OSError):
                    continue
            continue
    return "quote_time_missing", None


def _analyze_ticker_for_spread(
    j: Dict[str, Any],
    *,
    max_quote_age_sec: float = 120.0,
) -> Dict[str, Any]:
    """
    Derive spread fields from a Coinbase-style ticker dict.

    Never emits a fake ``9999`` bps as a "measurement" — use explicit status/reason fields instead.
    """
    bid = j.get("bid")
    ask = j.get("ask")
    price = j.get("price")

    try:
        bid_f = float(bid) if bid is not None else float("nan")
        ask_f = float(ask) if ask is not None else float("nan")
        price_f = float(price) if price is not None else float("nan")
    except (TypeError, ValueError):
        return {
            "spread_measurement_status": "unavailable",
            "spread_source": "coinbase_advanced_trade_ticker",
            "measured_spread_bps": None,
            "spread_unavailable_reason": "ticker_numeric_parse_error",
            "price_freshness_status": "unknown",
            "quote_age_sec": None,
            "candidate_excluded_due_to_missing_market_data": True,
            "selection_rejection_category": "missing_or_stale_quote",
            "mid": None,
        }

    mid: Optional[float]
    if not math.isnan(price_f) and price_f > 0:
        mid = price_f
    elif not math.isnan(bid_f) and not math.isnan(ask_f) and bid_f > 0 and ask_f > 0:
        mid = (bid_f + ask_f) / 2.0
    else:
        mid = None

    freshness, age_sec = _price_freshness_from_ticker(j)
    stale = age_sec is not None and age_sec > max_quote_age_sec

    if mid is None or mid <= 0 or math.isnan(mid):
        return {
            "spread_measurement_status": "unavailable",
            "spread_source": "coinbase_advanced_trade_ticker",
            "measured_spread_bps": None,
            "spread_unavailable_reason": "no_valid_mid_bid_ask",
            "price_freshness_status": freshness,
            "quote_age_sec": age_sec,
            "candidate_excluded_due_to_missing_market_data": True,
            "selection_rejection_category": "missing_or_stale_quote",
            "mid": mid,
        }

    if math.isnan(bid_f) or math.isnan(ask_f) or bid_f <= 0 or ask_f <= 0:
        return {
            "spread_measurement_status": "unavailable",
            "spread_source": "coinbase_advanced_trade_ticker",
            "measured_spread_bps": None,
            "spread_unavailable_reason": "bid_or_ask_missing_for_spread",
            "price_freshness_status": freshness,
            "quote_age_sec": age_sec,
            "candidate_excluded_due_to_missing_market_data": True,
            "selection_rejection_category": "missing_or_stale_quote",
            "mid": mid,
        }

    if stale:
        return {
            "spread_measurement_status": "unavailable",
            "spread_source": "coinbase_advanced_trade_ticker",
            "measured_spread_bps": None,
            "spread_unavailable_reason": f"quote_stale_gt_{max_quote_age_sec:.0f}s",
            "price_freshness_status": freshness,
            "quote_age_sec": age_sec,
            "candidate_excluded_due_to_missing_market_data": True,
            "selection_rejection_category": "missing_or_stale_quote",
            "mid": mid,
        }

    sp = (ask_f - bid_f) / mid * 10000.0
    return {
        "spread_measurement_status": "measured",
        "spread_source": "coinbase_advanced_trade_ticker",
        "measured_spread_bps": sp,
        "spread_unavailable_reason": None,
        "price_freshness_status": freshness,
        "quote_age_sec": age_sec,
        "candidate_excluded_due_to_missing_market_data": False,
        "selection_rejection_category": "none",
        "mid": mid,
    }


def run_gate_b_gainers_selection(
    *,
    runtime_root: Path,
    client: Any,
    capital_budget_usd: float = 100.0,
) -> Dict[str, Any]:
    from trading_ai.shark.outlets.coinbase import _brokerage_public_request

    root = resolve_ezras_runtime_root_for_daemon_authority(runtime_root)
    policy = load_gate_b_policy(runtime_root=root)
    max_spread = float(policy.get("max_spread_bps") or 80)
    max_quote_age = float(policy.get("max_ticker_age_sec") or 120.0)

    cands = ordered_validation_candidates()[:24]
    rows: List[Dict[str, Any]] = []
    summary_counts: Dict[str, int] = {
        "feed_error": 0,
        "missing_or_stale_quote": 0,
        "market_spread_policy": 0,
        "passed": 0,
    }

    for pid in cands:
        try:
            j = _brokerage_public_request(f"/market/products/{pid}/ticker")
        except Exception as exc:
            summary_counts["feed_error"] += 1
            rows.append(
                {
                    "product_id": pid,
                    "passed": False,
                    "spread_measurement_status": "unavailable",
                    "spread_source": "none",
                    "measured_spread_bps": None,
                    "spread_unavailable_reason": f"ticker_http_error:{type(exc).__name__}",
                    "price_freshness_status": "unknown",
                    "quote_age_sec": None,
                    "candidate_excluded_due_to_missing_market_data": True,
                    "selection_rejection_category": "feed_error",
                    "filters_failed": [f"ticker:{type(exc).__name__}"],
                    "filters_passed": False,
                }
            )
            continue
        if not isinstance(j, dict):
            summary_counts["feed_error"] += 1
            rows.append(
                {
                    "product_id": pid,
                    "passed": False,
                    "spread_measurement_status": "unavailable",
                    "spread_source": "none",
                    "measured_spread_bps": None,
                    "spread_unavailable_reason": "bad_ticker_json",
                    "price_freshness_status": "unknown",
                    "quote_age_sec": None,
                    "candidate_excluded_due_to_missing_market_data": True,
                    "selection_rejection_category": "feed_error",
                    "filters_failed": ["bad_ticker_json"],
                    "filters_passed": False,
                }
            )
            continue

        j = _normalize_coinbase_ticker_bid_ask(j)
        meta = _analyze_ticker_for_spread(j, max_quote_age_sec=max_quote_age)
        spread_measurement_status = meta["spread_measurement_status"]
        measured = meta["measured_spread_bps"]
        cat = str(meta["selection_rejection_category"])

        failed: List[str] = []
        if cat == "missing_or_stale_quote":
            summary_counts["missing_or_stale_quote"] += 1
            passed = False
            reason = meta.get("spread_unavailable_reason") or "missing_quote"
            failed.append(f"spread_unavailable:{reason}")
        elif spread_measurement_status == "measured" and measured is not None:
            ok_spread = measured <= max_spread
            if not ok_spread:
                failed.append(f"spread_bps_{measured:.2f}_gt_{max_spread}")
                summary_counts["market_spread_policy"] += 1
                passed = False
                cat = "market_spread_policy"
            else:
                passed = True
                summary_counts["passed"] += 1
        else:
            passed = False
            summary_counts["missing_or_stale_quote"] += 1
            failed.append("selection_state_unexpected")

        momentum_proxy = (
            abs(float(j.get("ask") or 0) - float(j.get("bid") or 0)) / float(meta["mid"] or 1.0)
            if meta.get("mid")
            else 0.0
        )

        rows.append(
            {
                "product_id": pid,
                "score": momentum_proxy if passed else -1e6,
                "passed": passed,
                "spread_measurement_status": spread_measurement_status,
                "spread_source": meta["spread_source"],
                "measured_spread_bps": measured,
                "spread_bps": measured,
                "spread_unavailable_reason": meta.get("spread_unavailable_reason"),
                "price_freshness_status": meta.get("price_freshness_status"),
                "quote_age_sec": meta.get("quote_age_sec"),
                "candidate_excluded_due_to_missing_market_data": bool(
                    meta.get("candidate_excluded_due_to_missing_market_data")
                ),
                "selection_rejection_category": cat,
                "momentum_proxy": momentum_proxy,
                "filters_passed": passed,
                "filters_failed": failed,
            }
        )

    viable = [r for r in rows if r.get("passed")]
    viable.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    selected = [r["product_id"] for r in viable[:8]]

    no_selection_reason: Optional[str] = None
    if not selected:
        if summary_counts["feed_error"] == len(cands):
            no_selection_reason = "all_candidates_feed_error"
        elif summary_counts["missing_or_stale_quote"] == len(cands):
            no_selection_reason = "all_candidates_missing_or_stale_quote"
        elif summary_counts["market_spread_policy"] > 0 and summary_counts["passed"] == 0:
            no_selection_reason = "all_candidates_rejected_by_spread_policy"
        else:
            no_selection_reason = "no_eligible_candidates_under_policy"

    snap = {
        "truth_version": "gate_b_selection_snapshot_v2",
        "selection_summary": {
            "counts_by_rejection_category": summary_counts,
            "no_selection_reason": no_selection_reason,
            "operator_note": (
                "measured_spread_bps is None when quote was missing, stale, or unparseable — "
                "not a real market spread of 9999 bps."
            ),
        },
        "ranked_gainer_candidates": rows,
        "selected_symbols": selected,
        "capital_budget_allocated_usd": float(capital_budget_usd),
        "policy": policy,
        "honesty": "Gate B gainers ranking uses spread + bid/ask momentum proxy only — not forward returns.",
    }
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json(_SNAPSHOT, snap)
    ad.write_text(_SNAPSHOT.replace(".json", ".txt"), json.dumps(snap, indent=2, default=str) + "\n")
    return snap
