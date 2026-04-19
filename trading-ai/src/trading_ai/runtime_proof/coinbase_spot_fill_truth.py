"""
Coinbase Advanced Trade **spot** fills: strict base vs quote interpretation for validation and close.

Quote-sized market BUY orders can return ``size`` in **USD** (matching ``filled_value``) instead of base
units. This module normalizes every fill and aggregates with explicit interpretation notes.

Executable invariants (no strategy logic):
- ``buy_base_qty`` = actual base asset received (e.g. BTC).
- ``buy_quote_spent`` = actual quote currency spent (e.g. USD).
- Flatten SELL size = ``round_down(buy_base_qty)`` to exchange increment — never quote notional as base.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from trading_ai.nte.execution.product_rules import round_base_to_increment

logger = logging.getLogger(__name__)

# Abort flatten if implied USD notional from (base × ref price) wildly exceeds buy quote spent (bug detector).
_MAX_IMPLIED_NOTIONAL_VS_QUOTE_SPENT = 3.0
_MAX_IMPLIED_NOTIONAL_EXTRA_USD = 50.0


@dataclass
class NormalizedCoinbaseFill:
    """One execution leg on a spot product, with explicit base/quote semantics."""

    side: str  # "BUY" | "SELL"
    product_id: str
    base_qty: float
    quote_qty: float
    avg_price: float
    fees_usd: float
    raw_fill: Dict[str, Any]
    interpretation_notes: List[str] = field(default_factory=list)


@dataclass
class SpotBuyAggregation:
    """Aggregated BUY fills for a single order."""

    product_id: str
    buy_base_qty: float
    buy_quote_spent: float
    fees_buy_usd: float
    avg_fill_price: float
    normalized_fills: List[NormalizedCoinbaseFill]
    confidence_notes: List[str] = field(default_factory=list)


@dataclass
class SpotSellAggregation:
    """Aggregated SELL fills for a single order."""

    product_id: str
    base_sold: float
    sell_quote_received: float
    fees_sell_usd: float
    avg_fill_price: float
    normalized_fills: List[NormalizedCoinbaseFill]
    confidence_notes: List[str] = field(default_factory=list)


class FlattenSizeValidationError(RuntimeError):
    """Flatten sell aborted: base/quote sanity failed."""

    code = "invalid_flatten_size_base_quote_mismatch"


def _float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fill_quote_from_raw(f: Mapping[str, Any]) -> float:
    for k in ("filled_value", "value", "quote_value"):
        if f.get(k) is not None:
            v = _float(f.get(k))
            if v != 0.0 or f.get(k) == 0:
                return abs(v)
    return 0.0


def _interpret_buy_leg_base_quote(
    product_id: str,
    raw: Mapping[str, Any],
) -> Tuple[float, float, float, List[str]]:
    """
    Return (base_qty, quote_qty, avg_price, notes) for one BUY fill.

    ``avg_price`` is quote per base from this leg (``quote_qty / base_qty`` when both > 0).
    """
    notes: List[str] = []
    price = abs(_float(raw.get("price") or raw.get("average_filled_price")))
    sz = abs(_float(raw.get("size") or raw.get("filled_size")))
    qv = _fill_quote_from_raw(raw)
    rel_eps = max(1e-9, 0.02 * qv) if qv > 0 else 1e-9

    base_qty = 0.0
    quote_qty = 0.0

    if price > 0 and qv > 0:
        from_quote = qv / price
        if abs(sz - qv) <= rel_eps:
            base_qty = from_quote
            quote_qty = qv
            notes.append("buy_leg:size_matches_quote_treating_size_as_quote_notional")
        else:
            implied_quote = sz * price
            if abs(implied_quote - qv) <= rel_eps or abs(implied_quote - qv) <= 0.02 * max(qv, 1.0):
                base_qty = sz
                quote_qty = qv
                notes.append("buy_leg:size_is_base_consistent_with_filled_value")
            else:
                base_qty = from_quote
                quote_qty = qv
                notes.append("buy_leg:prefer_quote_over_size_mismatch_using_filled_value_over_price")
    elif price > 0 and sz > 0:
        implied_quote = sz * price
        if implied_quote < price * 0.5 and implied_quote < 1e7:
            base_qty = sz
            quote_qty = implied_quote
            notes.append("buy_leg:infer_quote_as_size_times_price")
        else:
            base_qty = sz / price
            quote_qty = sz
            notes.append("buy_leg:treat_size_as_quote_notional_no_filled_value")
    else:
        base_qty = sz
        quote_qty = qv

    avg_px = (quote_qty / base_qty) if base_qty > 0 else price
    return base_qty, quote_qty, avg_px, notes


def _interpret_sell_leg_base_quote(
    product_id: str,
    raw: Mapping[str, Any],
) -> Tuple[float, float, float, List[str]]:
    """SELL: base is usually ``size``; quote is proceeds in USD."""
    notes: List[str] = []
    price = abs(_float(raw.get("price") or raw.get("average_filled_price")))
    sz = abs(_float(raw.get("size") or raw.get("filled_size")))
    qv = _fill_quote_from_raw(raw)
    rel_eps = max(1e-9, 0.02 * qv) if qv > 0 else 1e-9

    base_qty = 0.0
    quote_qty = 0.0

    if price > 0 and qv > 0:
        if abs(sz - qv) <= rel_eps:
            base_qty = qv / price
            quote_qty = qv
            notes.append("sell_leg:size_matches_quote_treating_size_as_quote_notional")
        else:
            implied = sz * price
            if abs(implied - qv) <= rel_eps or abs(implied - qv) <= 0.02 * max(qv, 1.0):
                base_qty = sz
                quote_qty = qv
                notes.append("sell_leg:size_is_base")
            else:
                base_qty = qv / price
                quote_qty = qv
                notes.append("sell_leg:prefer_filled_value_for_quote")
    elif price > 0 and sz > 0:
        quote_qty = sz * price
        base_qty = sz
        notes.append("sell_leg:quote_as_size_times_price")
    else:
        base_qty = sz
        quote_qty = qv

    avg_px = (quote_qty / base_qty) if base_qty > 0 else price
    return base_qty, quote_qty, avg_px, notes


def normalize_coinbase_buy_fills(
    product_id: str,
    raw_fills: Sequence[Mapping[str, Any]],
) -> SpotBuyAggregation:
    """Aggregate BUY order fills into ``buy_base_qty`` and ``buy_quote_spent`` (spot)."""
    pid = (product_id or "").strip().upper()
    total_base = 0.0
    total_quote = 0.0
    total_fee = 0.0
    norm: List[NormalizedCoinbaseFill] = []
    all_notes: List[str] = []

    for raw in raw_fills:
        rf = dict(raw)
        fee = abs(_float(rf.get("commission") or rf.get("fee")))
        total_fee += fee
        b, q, avgp, notes = _interpret_buy_leg_base_quote(pid, rf)
        total_base += b
        total_quote += q
        all_notes.extend(notes)
        side_u = str(rf.get("side") or "BUY").upper()
        norm.append(
            NormalizedCoinbaseFill(
                side=side_u,
                product_id=pid,
                base_qty=b,
                quote_qty=q,
                avg_price=avgp,
                fees_usd=fee,
                raw_fill=rf,
                interpretation_notes=notes,
            )
        )

    avg_fill = (total_quote / total_base) if total_base > 0 else 0.0
    # Cross-check: VWA base × avg vs quote
    if total_base > 0 and total_quote > 0 and avg_fill > 0:
        implied = total_base * avg_fill
        rel = abs(implied - total_quote) / max(total_quote, 1e-9)
        if rel > 0.05:
            all_notes.append(f"aggregate:base_quote_VWA_mismatch_rel_{rel:.4f}")

    return SpotBuyAggregation(
        product_id=pid,
        buy_base_qty=total_base,
        buy_quote_spent=total_quote,
        fees_buy_usd=total_fee,
        avg_fill_price=avg_fill,
        normalized_fills=norm,
        confidence_notes=all_notes,
    )


def normalize_coinbase_sell_fills(
    product_id: str,
    raw_fills: Sequence[Mapping[str, Any]],
) -> SpotSellAggregation:
    """Aggregate SELL order fills."""
    pid = (product_id or "").strip().upper()
    total_base = 0.0
    total_quote = 0.0
    total_fee = 0.0
    norm: List[NormalizedCoinbaseFill] = []
    all_notes: List[str] = []

    for raw in raw_fills:
        rf = dict(raw)
        fee = abs(_float(rf.get("commission") or rf.get("fee")))
        total_fee += fee
        b, q, avgp, notes = _interpret_sell_leg_base_quote(pid, rf)
        total_base += b
        total_quote += q
        all_notes.extend(notes)
        side_u = str(rf.get("side") or "SELL").upper()
        norm.append(
            NormalizedCoinbaseFill(
                side=side_u,
                product_id=pid,
                base_qty=b,
                quote_qty=q,
                avg_price=avgp,
                fees_usd=fee,
                raw_fill=rf,
                interpretation_notes=notes,
            )
        )

    avg_fill = (total_quote / total_base) if total_base > 0 else 0.0
    return SpotSellAggregation(
        product_id=pid,
        base_sold=total_base,
        sell_quote_received=total_quote,
        fees_sell_usd=total_fee,
        avg_fill_price=avg_fill,
        normalized_fills=norm,
        confidence_notes=all_notes,
    )


def validate_flatten_base_before_sell(
    *,
    product_id: str,
    raw_base_qty_bought: float,
    rounded_base_str: str,
    buy_quote_spent: float,
    ref_price_usd_per_base: float,
    quote_notional_request: float,
) -> None:
    """
    Hard guards before placing market SELL. Raises :class:`FlattenSizeValidationError` if
    base clearly cannot match the observed quote spent (e.g. quote mistaken for base).
    """
    try:
        rb = float(rounded_base_str)
    except (TypeError, ValueError) as exc:
        raise FlattenSizeValidationError("invalid_flatten_size_base_quote_mismatch:rounded_parse") from exc

    if raw_base_qty_bought <= 0 or rb <= 0:
        raise FlattenSizeValidationError(
            "invalid_flatten_size_base_quote_mismatch:non_positive_base_qty"
        )

    if ref_price_usd_per_base <= 0:
        raise FlattenSizeValidationError(
            "invalid_flatten_size_base_quote_mismatch:missing_ref_price"
        )

    implied_notional = rb * ref_price_usd_per_base
    cap = max(
        buy_quote_spent * _MAX_IMPLIED_NOTIONAL_VS_QUOTE_SPENT,
        buy_quote_spent + _MAX_IMPLIED_NOTIONAL_EXTRA_USD,
    )
    if buy_quote_spent > 0 and implied_notional > cap:
        raise FlattenSizeValidationError(
            "invalid_flatten_size_base_quote_mismatch:implied_notional_exceeds_buy_quote_bounds "
            f"implied_usd={implied_notional:.2f} buy_quote_spent={buy_quote_spent:.2f} cap={cap:.2f}"
        )

    # Small-validation heuristic: ~$10 BTC buy must stay sub-fractional BTC
    if product_id.upper().startswith("BTC-") and quote_notional_request <= 25.0 and rb >= 0.01:
        raise FlattenSizeValidationError(
            "invalid_flatten_size_base_quote_mismatch:btc_sub_25usd_implies_sub_centibtc "
            f"rounded_base={rounded_base_str!r} quote_request={quote_notional_request}"
        )


def log_flatten_sizing(
    *,
    product_id: str,
    raw_base_qty: float,
    rounded_base_str: str,
    ref_price_usd_per_base: float,
    buy_quote_spent: float,
) -> None:
    logger.info(
        "live_validation_flatten: product=%s raw_base_qty=%.12f rounded_base_str=%s "
        "ref_price=%.2f buy_quote_spent=%.4f",
        product_id,
        raw_base_qty,
        rounded_base_str,
        ref_price_usd_per_base,
        buy_quote_spent,
    )


def dry_run_validation_close_from_fixtures(
    product_id: str,
    quote_notional_usd: float,
    buy_raw_fills: List[Dict[str, Any]],
    sell_raw_fills: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    No live orders: run buy aggregation, rounding, guards, optional sell + realized PnL.

    For tests and operator proof that quote-as-base bugs cannot reach ``place_market_sell``.
    """
    from trading_ai.global_layer.realized_pnl import compute_realized_pnl

    buy_agg = normalize_coinbase_buy_fills(product_id, buy_raw_fills)
    rounded = round_base_to_increment(product_id, buy_agg.buy_base_qty)
    ref_px = buy_agg.avg_fill_price
    err: Optional[str] = None
    try:
        validate_flatten_base_before_sell(
            product_id=product_id,
            raw_base_qty_bought=buy_agg.buy_base_qty,
            rounded_base_str=rounded,
            buy_quote_spent=buy_agg.buy_quote_spent,
            ref_price_usd_per_base=ref_px,
            quote_notional_request=quote_notional_usd,
        )
    except FlattenSizeValidationError as exc:
        err = str(exc)

    sell_quote = 0.0
    fees_sell = 0.0
    gross = None
    net = None
    if sell_raw_fills:
        sell_agg = normalize_coinbase_sell_fills(product_id, sell_raw_fills)
        sell_quote = sell_agg.sell_quote_received
        fees_sell = sell_agg.fees_sell_usd
        pnl = compute_realized_pnl(
            {
                "instrument_kind": "spot",
                "buy_quote_spent": buy_agg.buy_quote_spent,
                "sell_quote_received": sell_quote,
                "fees_total": buy_agg.fees_buy_usd + fees_sell,
                "fields_complete": True,
            }
        )
        gross = pnl.gross_pnl
        net = pnl.net_pnl

    return {
        "product_id": product_id,
        "quote_notional_request": quote_notional_usd,
        "buy_aggregation": {
            "buy_base_qty": buy_agg.buy_base_qty,
            "buy_quote_spent": buy_agg.buy_quote_spent,
            "fees_buy_usd": buy_agg.fees_buy_usd,
            "avg_fill_price": buy_agg.avg_fill_price,
            "confidence_notes": buy_agg.confidence_notes,
        },
        "rounded_base_str": rounded,
        "flatten_validation_error": err,
        "sell_quote_received": sell_quote,
        "fees_sell_usd": fees_sell,
        "gross_pnl": gross,
        "net_pnl": net,
    }
