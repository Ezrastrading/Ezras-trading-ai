"""Schema version, avenue registry, trade event validation — Trade Intelligence Databank."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

DATABANK_SCHEMA_VERSION = "1.2.1"

# Locked globally — do not alias elsewhere (Section 8).
AVENUE_REGISTRY: Dict[str, str] = {
    "A": "coinbase",
    "B": "kalshi",
    "C": "tastytrade",
}


def normalize_avenue(avenue_id: str, avenue_name: str) -> Tuple[str, str]:
    """Return canonical (avenue_id, avenue_name); raises ValueError if invalid."""
    aid = (avenue_id or "").strip().upper()
    aname = (avenue_name or "").strip().lower()
    if aid not in AVENUE_REGISTRY:
        raise ValueError(f"unknown avenue_id {avenue_id!r}; expected one of {sorted(AVENUE_REGISTRY)}")
    expected = AVENUE_REGISTRY[aid]
    if aname != expected:
        raise ValueError(f"avenue_name must be {expected!r} for {aid}, got {avenue_name!r}")
    return aid, aname


_TRADE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{4,128}$")


def _require(d: Mapping[str, Any], key: str, errors: List[str]) -> None:
    if key not in d or d[key] is None or (isinstance(d[key], str) and not str(d[key]).strip()):
        errors.append(f"missing_or_empty:{key}")


def validate_trade_event_payload(raw: Mapping[str, Any]) -> List[str]:
    """Return list of validation error codes (empty = ok)."""
    errors: List[str] = []
    _require(raw, "trade_id", errors)
    tid = raw.get("trade_id")
    if tid is not None and isinstance(tid, str) and not _TRADE_ID_RE.match(tid):
        errors.append("invalid_trade_id_format")
    _require(raw, "avenue_id", errors)
    _require(raw, "avenue_name", errors)
    _require(raw, "asset", errors)
    _require(raw, "strategy_id", errors)
    _require(raw, "route_chosen", errors)
    _require(raw, "regime", errors)
    _require(raw, "timestamp_open", errors)
    _require(raw, "timestamp_close", errors)
    if raw.get("avenue_id") and raw.get("avenue_name"):
        try:
            normalize_avenue(str(raw["avenue_id"]), str(raw["avenue_name"]))
        except ValueError as e:
            errors.append(f"avenue:{e}")
    return errors


def merge_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing optional columns with neutral defaults for storage/scoring."""
    base: Dict[str, Any] = dict(raw)
    defaults = {
        "route_a_score": None,
        "route_b_score": None,
        "rejected_route": None,
        "rejected_reason": None,
        "spread_bps_entry": 0.0,
        "volatility_bps_entry": 0.0,
        "expected_edge_bps": 0.0,
        "expected_fee_bps": 0.0,
        "expected_net_edge_bps": 0.0,
        "intended_entry_price": 0.0,
        "actual_entry_price": 0.0,
        "entry_slippage_bps": 0.0,
        "entry_order_type": "unknown",
        "maker_taker": "unknown",
        "fill_seconds": 0.0,
        "partial_fill_count": 0,
        "stale_cancelled": False,
        "intended_exit_price": 0.0,
        "actual_exit_price": 0.0,
        "exit_reason": "",
        "exit_slippage_bps": 0.0,
        "hold_seconds": 0.0,
        "gross_pnl": 0.0,
        "fees_paid": 0.0,
        "net_pnl": 0.0,
        "shadow_price": None,
        "shadow_diff_bps": None,
        "discipline_ok": True,
        "degraded_mode": False,
        "health_state": "ok",
        "anomaly_flags": [],
        "reward_delta": 0.0,
        "penalty_delta": 0.0,
        "edge_id": None,
        "edge_lane": None,
        "edge_status_at_trade": None,
        "market_snapshot_json": None,
        "instrument_kind": None,
        "base_qty": None,
        "quote_qty_buy": None,
        "quote_qty_sell": None,
        "avg_entry_price": None,
        "avg_exit_price": None,
        "contracts": None,
        "entry_price_per_contract": None,
        "payout_per_contract": None,
        "entry_premium": None,
        "exit_premium": None,
        "option_multiplier": None,
        "latency_ms": None,
        "regime_bucket": None,
        "execution_quality_score": None,
        "research_source": None,
        "schema_version": DATABANK_SCHEMA_VERSION,
    }
    for k, v in defaults.items():
        base.setdefault(k, v)
    if not base.get("created_at"):
        base["created_at"] = _utc_now_iso()
    if not isinstance(base.get("anomaly_flags"), list):
        base["anomaly_flags"] = []
    rc = base.get("ratio_context")
    if rc is not None and base.get("market_snapshot_json") is None:
        base["market_snapshot_json"] = {"ratio_context": rc}
    return base


def fold_ratio_context_into_merged(merged: Mapping[str, Any], ratio_context: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach ``ratio_context`` under ``market_snapshot_json`` without dropping other snapshot keys."""
    out: Dict[str, Any] = dict(merged)
    msj = out.get("market_snapshot_json")
    if not isinstance(msj, dict):
        msj = {}
    prev = msj.get("ratio_context") if isinstance(msj.get("ratio_context"), dict) else {}
    msj = {**msj, "ratio_context": {**prev, **dict(ratio_context)}}
    out["market_snapshot_json"] = msj
    return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_for_supabase_trade_events(merged: Dict[str, Any], scores: Mapping[str, Any]) -> Dict[str, Any]:
    """Flatten merged event + scores into trade_events table row."""
    keys = [
        "trade_id",
        "avenue_id",
        "avenue_name",
        "asset",
        "strategy_id",
        "route_chosen",
        "route_a_score",
        "route_b_score",
        "rejected_route",
        "rejected_reason",
        "regime",
        "spread_bps_entry",
        "volatility_bps_entry",
        "expected_edge_bps",
        "expected_fee_bps",
        "expected_net_edge_bps",
        "intended_entry_price",
        "actual_entry_price",
        "entry_slippage_bps",
        "entry_order_type",
        "maker_taker",
        "fill_seconds",
        "partial_fill_count",
        "stale_cancelled",
        "intended_exit_price",
        "actual_exit_price",
        "exit_reason",
        "exit_slippage_bps",
        "hold_seconds",
        "gross_pnl",
        "fees_paid",
        "net_pnl",
        "shadow_price",
        "shadow_diff_bps",
        "discipline_ok",
        "degraded_mode",
        "health_state",
        "execution_score",
        "edge_score",
        "discipline_score",
        "trade_quality_score",
        "reward_delta",
        "penalty_delta",
        "anomaly_flags",
        "timestamp_open",
        "timestamp_close",
        "created_at",
        "edge_id",
        "edge_lane",
        "edge_status_at_trade",
        "market_snapshot_json",
        "instrument_kind",
        "base_qty",
        "quote_qty_buy",
        "quote_qty_sell",
        "avg_entry_price",
        "avg_exit_price",
        "contracts",
        "entry_price_per_contract",
        "payout_per_contract",
        "entry_premium",
        "exit_premium",
        "option_multiplier",
        "latency_ms",
        "regime_bucket",
        "execution_quality_score",
        "research_source",
        "schema_version",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        if k in ("execution_score", "edge_score", "discipline_score", "trade_quality_score"):
            out[k] = scores.get(k, merged.get(k))
        else:
            out[k] = merged.get(k)
    out["schema_version"] = DATABANK_SCHEMA_VERSION
    return out
