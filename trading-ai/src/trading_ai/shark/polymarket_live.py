"""Polymarket CLOB live order + EIP-712 signing."""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any, Dict

from trading_ai.shark.models import ExecutionIntent, OrderResult

logger = logging.getLogger(__name__)

CLOB_ORDER_URL = os.environ.get("POLY_CLOB_ORDER_URL", "https://clob.polymarket.com/order")


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


def submit_polymarket_order(intent: ExecutionIntent) -> OrderResult:
    key = (os.environ.get("POLY_WALLET_KEY") or "").strip()
    api_key = (os.environ.get("POLY_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "Polymarket execution unavailable: POLY_WALLET_KEY unset (set wallet key for CLOB orders)."
        )
    if not api_key:
        logger.warning("POLY_API_KEY empty — submitting order with public CLOB headers only")
    from eth_account import Account

    pk = key[2:] if key.startswith("0x") else key
    acct = Account.from_key("0x" + pk)
    maker = acct.address
    token_id = str(intent.meta.get("token_id") or intent.market_id.replace("poly:", "").replace("demo-", "1"))
    notional = max(1, int(intent.notional_usd * 1_000_000))
    shares = max(1, int(intent.shares))
    sig = sign_polymarket_order_eip712(
        private_key_hex=key,
        maker=maker,
        token_id=token_id,
        maker_amount=notional,
        taker_amount=shares,
        side_buy=(intent.side.lower() == "yes"),
    )
    body = {
        "order": {
            "salt": random.randint(1, 2**32),
            "maker": maker,
            "signer": maker,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": token_id,
            "makerAmount": notional,
            "takerAmount": shares,
            "expiration": int(time.time()) + 300,
            "nonce": 0,
            "feeRateBps": 0,
            "side": "BUY",
            "signatureType": 0,
            "signature": sig,
        }
    }
    from trading_ai.shark.outlets.polymarket import CLOB_SIGN_PATH_ORDER, build_polymarket_l2_headers

    body_str = json.dumps(body, separators=(",", ":"))
    hdrs: Dict[str, str] = {"User-Agent": "EzrasShark/1.0"}
    if (os.environ.get("POLY_API_SECRET") or "").strip() and (os.environ.get("POLY_WALLET_KEY") or "").strip():
        hdrs.update(
            build_polymarket_l2_headers(
                "POST",
                CLOB_SIGN_PATH_ORDER,
                serialized_body=body_str,
            )
        )
    else:
        hdrs["Content-Type"] = "application/json"
        if api_key:
            hdrs["POLY_API_KEY"] = api_key
    req = urllib.request.Request(
        CLOB_ORDER_URL,
        data=body_str.encode("utf-8"),
        method="POST",
        headers=hdrs,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            j = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
        except Exception:
            j = {"error": str(e)}
        raise RuntimeError(f"Polymarket order failed: {j}") from e
    oid = str(j.get("orderID") or j.get("id") or j.get("order_id") or "")
    return OrderResult(
        order_id=oid or "unknown",
        filled_price=float(intent.expected_price),
        filled_size=float(intent.shares),
        timestamp=time.time(),
        status=str(j.get("status") or "submitted"),
        outlet="polymarket",
        raw=j,
    )
