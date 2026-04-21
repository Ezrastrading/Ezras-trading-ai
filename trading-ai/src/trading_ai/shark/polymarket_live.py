"""Polymarket CLOB live orders via py-clob-client (L1 sign + L2 API creds)."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, Dict

from trading_ai.shark.models import ExecutionIntent, OrderResult

logger = logging.getLogger(__name__)

# Limit BUY: bid at current touch + small slippage (never market orders).
_SLIPPAGE = 0.001


def limit_price_with_slippage(base_price: float) -> float:
    """Cap price in (0,1); add ``_SLIPPAGE`` for resting limit."""
    return float(max(1e-6, min(1.0 - 1e-6, float(base_price) + _SLIPPAGE)))


def sign_polymarket_order_eip712(
    *,
    private_key_hex: str,
    maker: str,
    token_id: str,
    maker_amount: int,
    taker_amount: int,
    side_buy: bool = True,
    chain_id: int = 137,
) -> str:
    """
    EIP-712 typed-data signature (eth-account). Falls back to structured hash + sign if encoding fails.
    Requires: pip install eth-account
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct, encode_typed_data
    except ImportError as e:
        raise ImportError("polymarket EIP-712 requires eth-account: pip install eth-account") from e

    pk = private_key_hex.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    acct = Account.from_key("0x" + pk)

    full_message: Dict[str, Any] = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "tokenId", "type": "string"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "side", "type": "string"},
            ],
        },
        "primaryType": "Order",
        "domain": {"name": "Polymarket CLOB", "version": "1", "chainId": chain_id},
        "message": {
            "salt": random.randint(1, 2**48),
            "maker": maker,
            "tokenId": str(token_id),
            "makerAmount": str(maker_amount),
            "takerAmount": str(taker_amount),
            "side": "BUY" if side_buy else "SELL",
        },
    }
    enc = None
    try:
        enc = encode_typed_data(full_message=full_message)
    except Exception:
        blob = json.dumps(full_message["message"], sort_keys=True)
        enc = encode_defunct(text="EIP712_FALLBACK|" + blob)

    signed = acct.sign_message(enc)
    sig = signed.signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return sig


def _clob_client() -> Any:
    from py_clob_client.client import ClobClient

    key = (os.environ.get("POLY_WALLET_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "Polymarket execution unavailable: POLY_WALLET_KEY unset (set wallet key for CLOB orders)."
        )
    raw_key = key if key.startswith("0x") else ("0x" + key)
    host = (os.environ.get("POLY_CLOB_BASE") or "https://clob.polymarket.com").rstrip("/")
    client = ClobClient(host=host, chain_id=137, key=raw_key)
    creds = client.create_or_derive_api_creds()
    if creds is None:
        raise RuntimeError("Polymarket: create_or_derive_api_creds returned None")
    return ClobClient(host=host, chain_id=137, key=raw_key, creds=creds)


def _post_one_limit(client: Any, token_id: str, base_price: float, size: float) -> Any:
    from py_clob_client.clob_types import OrderArgs

    order_args = OrderArgs(
        token_id=str(token_id),
        price=limit_price_with_slippage(base_price),
        size=max(1.0, float(size)),
        side="BUY",
    )
    return client.create_and_post_order(order_args)


def _resp_to_parts(resp: Any) -> tuple[str, str, Any]:
    if isinstance(resp, dict):
        oid = str(resp.get("orderID") or resp.get("order_id") or resp.get("id") or "")
        status = str(resp.get("status") or "submitted")
        raw: Any = resp
    else:
        oid = str(getattr(resp, "orderID", None) or getattr(resp, "order_id", "") or "")
        status = "submitted"
        raw = {"response": repr(resp)}
    return oid, status, raw


def submit_polymarket_order(intent: ExecutionIntent) -> OrderResult:
    """Sign and post limit order(s) via ``ClobClient.create_and_post_order`` (GTC limit, not market)."""
    client = _clob_client()
    u = intent.meta or {}

    if u.get("pure_arbitrage_dual"):
        y = u.get("yes_leg") or {}
        n = u.get("no_leg") or {}
        yt, nt = y.get("token_id"), n.get("token_id")
        if not yt or not nt:
            raise RuntimeError("pure_arbitrage_dual missing yes_leg/no_leg token_ids")
        try:
            r1 = _post_one_limit(client, str(yt), float(y.get("limit_price", 0)), float(y.get("size", 1)))
            r2 = _post_one_limit(client, str(nt), float(n.get("limit_price", 0)), float(n.get("size", 1)))
        except Exception as exc:
            logger.error("Poly dual order FAILED: %s", exc)
            logger.exception("Polymarket create_and_post_order failed")
            raise RuntimeError(f"Polymarket order failed: {exc}") from exc
        id1, st1, raw1 = _resp_to_parts(r1)
        id2, st2, raw2 = _resp_to_parts(r2)
        logger.info("Poly dual limit orders placed: %s %s", id1, id2)
        sz = float(y.get("size", intent.shares)) + float(n.get("size", intent.shares))
        return OrderResult(
            order_id=f"dual:{id1}:{id2}",
            filled_price=float(intent.expected_price),
            filled_size=sz,
            timestamp=time.time(),
            status=st1 if st1 == st2 else f"{st1}|{st2}",
            outlet="polymarket",
            raw={"yes": raw1, "no": raw2},
        )

    token_id = u.get("token_id")
    if not token_id:
        token_id = str(intent.market_id).replace("poly:", "").replace("demo-", "")
    token_id = str(token_id)

    base_px = float(intent.expected_price)
    size = max(1.0, float(intent.shares))
    try:
        resp = _post_one_limit(client, token_id, base_px, size)
    except Exception as exc:
        logger.error("Poly order FAILED: %s", exc)
        logger.exception("Polymarket create_and_post_order failed")
        raise RuntimeError(f"Polymarket order failed: {exc}") from exc

    oid, status, raw = _resp_to_parts(resp)
    logger.info("Poly limit order placed: %s", resp)
    return OrderResult(
        order_id=oid or "unknown",
        filled_price=limit_price_with_slippage(base_px),
        filled_size=float(size),
        timestamp=time.time(),
        status=status,
        outlet="polymarket",
        raw=raw if isinstance(raw, dict) else {"raw": raw},
    )
