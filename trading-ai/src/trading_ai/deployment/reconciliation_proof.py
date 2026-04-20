"""
Exchange vs internal reconciliation proof (Coinbase spot).

Supports:

- **strict_absolute** — legacy: ``|exchange_base - internal_base|`` must be within tolerance.
  Fails when the exchange holds preexisting base inventory not reflected in internal open positions.

- **inventory_delta** — preferred for live micro-validation: pass ``baseline_exchange_base_qty`` and
  ``baseline_internal_base_qty`` captured immediately before the round trip; we verify that the
  *change* in exchange base matches the *change* in internally tracked base (both should be ~0 after
  a flat round trip, even if the account started with dust or prior inventory).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping

from trading_ai.deployment.paths import reconciliation_proof_jsonl_path
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.nte.spot_inventory_snapshot import (
    exchange_currency_qty,
    internal_open_base_qty_for_asset,
    parse_spot_product,
    usd_usdc_from_accounts,
)
from trading_ai.shark.state_store import load_positions


def _internal_coinbase_base_equiv(positions: Mapping[str, Any], base_ccy: str) -> float:
    """Deprecated name: use :func:`internal_open_base_qty_for_asset`."""
    return internal_open_base_qty_for_asset(positions, base_ccy)


def _usd_usdc_from_accounts(accounts: List[Dict[str, Any]]) -> float:
    _u, _c, comb = usd_usdc_from_accounts(accounts)
    return comb


def prove_reconciliation_after_trade(
    trade_context: Mapping[str, Any],
    *,
    append_log: bool = True,
    btc_tolerance: float = 1e-4,
    quote_tolerance_usd: float = 25.0,
) -> Dict[str, Any]:
    """
    Compare exchange balances vs internal state after a round trip.

    ``trade_context`` may include:
      - ``product_id`` (e.g. BTC-USD)
      - ``baseline_exchange_base_qty`` / ``baseline_internal_base_qty`` — enables inventory_delta mode
      - ``reconciliation_mode`` — ``inventory_delta`` | ``strict_absolute`` (optional; inferred if baselines present)
      - ``expected_quote_delta_usd`` optional
      - ``internal_open_order_ids`` optional list (expected no opens after flat)
    """
    product_id = str(trade_context.get("product_id") or "BTC-USD").strip()
    base_ccy, _quote_ccy = parse_spot_product(product_id)

    mode_override = str(trade_context.get("reconciliation_mode") or "").strip().lower()
    if not mode_override:
        be = trade_context.get("baseline_exchange_base_qty")
        bi = trade_context.get("baseline_internal_base_qty")
        mode = "inventory_delta" if be is not None and bi is not None else "strict_absolute"
    elif mode_override in ("inventory_delta", "delta", "controlled_preexisting_inventory"):
        mode = "inventory_delta"
    elif mode_override in ("strict_absolute", "strict_flat_start"):
        mode = "strict_absolute"
    else:
        mode = "inventory_delta" if (
            trade_context.get("baseline_exchange_base_qty") is not None
            and trade_context.get("baseline_internal_base_qty") is not None
        ) else "strict_absolute"

    rec: Dict[str, Any] = {
        "ts": iso_now(),
        "product_id": product_id,
        "validation_base_asset": base_ccy,
        "validation_quote_asset": _quote_ccy,
        "reconciliation_ok": False,
        "reconciliation_mode_used": mode,
        "reconciliation_notes": [],
        "notes": [],
    }

    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        cc = CoinbaseClient()
        if not cc.has_credentials():
            rec["notes"].append("coinbase_credentials_missing")
            rec["reconciliation_notes"].append("coinbase_credentials_missing")
            _append_rec(rec, append_log)
            return rec
        accounts = cc.list_all_accounts()
    except Exception as exc:
        rec["notes"].append(f"coinbase_fetch_failed:{type(exc).__name__}")
        rec["reconciliation_notes"].append(rec["notes"][-1])
        _append_rec(rec, append_log)
        return rec

    ex_btc = exchange_currency_qty(accounts, base_ccy)
    ex_quote = _usd_usdc_from_accounts(accounts)
    positions = load_positions()
    in_btc = _internal_coinbase_base_equiv(positions, base_ccy)

    rec["exchange_base_qty"] = ex_btc
    rec["exchange_usd_usdc"] = ex_quote
    rec["internal_open_base_qty"] = in_btc

    be = trade_context.get("baseline_exchange_base_qty")
    bi = trade_context.get("baseline_internal_base_qty")
    if be is not None:
        try:
            rec["exchange_base_qty_before"] = float(be)
        except (TypeError, ValueError):
            rec["exchange_base_qty_before"] = None
    if bi is not None:
        try:
            rec["internal_base_qty_before"] = float(bi)
        except (TypeError, ValueError):
            rec["internal_base_qty_before"] = None

    if mode == "inventory_delta" and be is not None and bi is not None:
        try:
            be_f = float(be)
            bi_f = float(bi)
        except (TypeError, ValueError):
            rec["notes"].append("invalid_baseline_qty_non_numeric")
            rec["reconciliation_notes"].append(rec["notes"][-1])
        else:
            d_ex = ex_btc - be_f
            d_in = in_btc - bi_f
            rec["delta_exchange_base"] = d_ex
            rec["delta_internal_base"] = d_in
            rec["imported_inventory_baseline"] = bool(trade_context.get("imported_inventory_baseline"))
            if abs(d_ex - d_in) > btc_tolerance:
                rec["notes"].append(
                    f"base_delta_mismatch_exchange_vs_internal abs_delta>{btc_tolerance} "
                    f"(delta_ex={d_ex!r} delta_in={d_in!r})"
                )
                rec["reconciliation_notes"].append(
                    "Round-trip did not preserve inventory delta: exchange vs internal base change disagree. "
                    "If the account had preexisting base not tracked internally, use baseline snapshot mode "
                    "or import exchange inventory as initial state."
                )
    else:
        # strict_absolute — legacy matrix
        if abs(ex_btc - in_btc) > btc_tolerance:
            rec["notes"].append(
                f"base_mismatch_exchange_vs_internal abs_delta>{btc_tolerance} "
                f"(exchange={ex_btc!r} internal={in_btc!r})"
            )
            rec["reconciliation_notes"].append(
                "Absolute base qty mismatch. Common cause: preexisting exchange inventory while internal "
                "open positions show zero — pass baseline_exchange_base_qty and baseline_internal_base_qty "
                "from a snapshot taken immediately before the validation round trip."
            )

    bq = trade_context.get("buy_quote_spent")
    sq = trade_context.get("sell_quote_received")
    if bq is not None and sq is not None:
        try:
            rec["quote_flow_audit"] = {
                "buy_quote_spent": float(bq),
                "sell_quote_received": float(sq),
                "net_quote_usd": float(sq) - float(bq),
            }
        except (TypeError, ValueError):
            rec["quote_flow_audit"] = {"error": "non_numeric_quote_fields"}

    exp_d = trade_context.get("expected_quote_delta_usd")
    if exp_d is not None:
        try:
            exp_d = float(exp_d)
            if abs(exp_d) > quote_tolerance_usd * 10:
                rec["notes"].append("quote_delta_sanity_advisory_only")
        except (TypeError, ValueError):
            pass

    internal_orders = trade_context.get("internal_open_order_ids") or []
    if internal_orders:
        rec["notes"].append("internal_open_orders_expected_empty")

    open_ex = 0
    try:
        open_ex = int(trade_context.get("exchange_open_orders_count") or 0)
    except (TypeError, ValueError):
        open_ex = 0
    if open_ex > 0:
        rec["notes"].append("exchange_open_orders_nonzero")

    if bool(trade_context.get("oversell_risk")):
        rec["notes"].append("oversell_risk_flag")

    strict_flat = (os.environ.get("SPOT_STRICT_FLAT_START") or "").strip().lower() in ("1", "true", "yes")
    if strict_flat and mode == "strict_absolute" and ex_btc > btc_tolerance and in_btc <= btc_tolerance:
        rec["notes"].append("strict_flat_start_failed:exchange_holds_base_without_internal_position")
        rec["reconciliation_notes"].append(
            "SPOT_STRICT_FLAT_START: exchange reports base inventory but internal tracker has none — "
            "import inventory, clear dust manually, or use inventory_delta mode with baselines."
        )

    rec["reconciliation_ok"] = len(rec["notes"]) == 0
    _append_rec(rec, append_log)
    return rec


def _append_rec(rec: Dict[str, Any], append_log: bool) -> None:
    if not append_log:
        return
    p = reconciliation_proof_jsonl_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
