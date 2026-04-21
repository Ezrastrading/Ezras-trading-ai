"""Manifold live bet placement."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from trading_ai.shark.models import ExecutionIntent, OrderResult

logger = logging.getLogger(__name__)

API = os.environ.get("MANIFOLD_API_BASE", "https://api.manifold.markets/v0")


def submit_manifold_bet(intent: ExecutionIntent) -> OrderResult:
    from trading_ai.shark.required_env import require_manifold_api_key

    key = require_manifold_api_key()
    contract_id = intent.market_id.replace("manifold:", "")
    outcome = "YES" if intent.side.lower() == "yes" else "NO"
    body = {
        "amount": round(intent.notional_usd, 2),
        "contractId": contract_id,
        "outcome": outcome,
    }
    req = urllib.request.Request(
        f"{API.rstrip('/')}/bet",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Key {key}",
            "User-Agent": "EzrasShark/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            j = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
        except Exception:
            j = {"error": str(e)}
        raise RuntimeError(f"Manifold bet failed: {j}") from e
    oid = str(j.get("betId") or j.get("id") or "")
    return OrderResult(
        order_id=oid or "unknown",
        filled_price=float(intent.expected_price),
        filled_size=float(intent.shares),
        timestamp=time.time(),
        status="filled",
        outlet="manifold",
        raw=j,
    )
