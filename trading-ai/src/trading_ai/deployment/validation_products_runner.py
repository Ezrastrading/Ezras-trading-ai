"""Shared validation-products resolution (CLI + live loop)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.nte.config.settings import load_nte_settings
from trading_ai.nte.hardening.coinbase_product_policy import (
    coinbase_product_nte_allowed,
    default_live_validation_product_priority,
    ordered_validation_candidates,
)
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.runtime_proof.coinbase_accounts import (
    get_available_quote_balances,
    resolve_validation_market_product,
)
from trading_ai.shark.outlets.coinbase import CoinbaseClient


def run_validation_products(*, runtime_root: Optional[Path] = None, quote_notional: float = 10.0) -> Dict[str, Any]:
    """
    Resolve validation product priority, allowlist checks, balances, and chosen product (writes control artifacts
    when credentials work — same as ``python -m trading_ai.deployment validation-products``).
    """
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(root))

    priority = list(default_live_validation_product_priority())
    cand = ordered_validation_candidates()
    nte = load_nte_settings()
    payload: Dict[str, Any] = {
        "LIVE_VALIDATION_PRODUCT_PRIORITY": priority,
        "nte_allowlist_check": {p: coinbase_product_nte_allowed(p) for p in cand},
        "nte_allowed_products_canonical": list(nte.products),
    }
    try:
        c = CoinbaseClient()
        payload["quote_balances"] = get_available_quote_balances(c)
        ch, diag, err = resolve_validation_market_product(
            c,
            quote_notional=float(quote_notional),
            write_control_artifacts=True,
        )
        payload["resolve_quote_usd"] = float(quote_notional)
        resolve_block = {
            "chosen_product_id": ch,
            "chosen": ch,
            "error": err,
            "diagnostics": diag,
        }
        payload["resolve"] = resolve_block
        payload["resolve_10_usd"] = resolve_block
        payload["selector_aligned_with_guard"] = err is None and bool(ch and coinbase_product_nte_allowed(ch))
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload
