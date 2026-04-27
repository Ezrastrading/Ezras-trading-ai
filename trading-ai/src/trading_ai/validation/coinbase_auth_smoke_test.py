"""
Coinbase auth smoke test - fetches balances only, no orders.

Outputs AUTH_PASS / AUTH_FAIL with exact reason.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def coinbase_auth_smoke_test() -> dict:
    """
    Smoke test Coinbase auth by fetching balances only.
    
    Returns dict with status and reason.
    """
    try:
        from trading_ai.shark.coinbase_tracker import get_coinbase_balance
        
        balance = get_coinbase_balance()
        
        if not balance:
            return {
                "status": "AUTH_FAIL",
                "reason": "balance_fetch_returned_none",
            }
        
        source = balance.get("source", "")
        
        if source in ("no_credentials", "fetch_failed", "http_error_401", "http_error_403"):
            return {
                "status": "AUTH_FAIL",
                "reason": f"balance_fetch_failed_source_{source}",
            }
        
        usdc = float(balance.get("usdc", 0.0) or 0.0)
        eth_usd = float(balance.get("eth_usd_value", 0.0) or 0.0)
        total_usd = usdc + eth_usd
        
        if total_usd <= 0:
            return {
                "status": "AUTH_FAIL",
                "reason": f"balance_zero_usdc={usdc}_eth_usd={eth_usd}",
            }
        
        return {
            "status": "AUTH_PASS",
            "reason": "balance_fetch_success",
            "available_usd": total_usd,
            "usdc": usdc,
            "eth_usd_value": eth_usd,
            "source": source,
        }
        
    except Exception as exc:
        logger.exception("Coinbase auth smoke test exception")
        return {
            "status": "AUTH_FAIL",
            "reason": f"exception_{type(exc).__name__}",
            "error": str(exc),
        }


def main() -> None:
    """Run smoke test and print result."""
    result = coinbase_auth_smoke_test()
    
    print("=" * 80)
    print("COINBASE AUTH SMOKE TEST")
    print("=" * 80)
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    
    if result['status'] == 'AUTH_PASS':
        print(f"Available USD: ${result.get('available_usd', 0.0):.2f}")
        print(f"USDC: ${result.get('usdc', 0.0):.2f}")
        print(f"ETH USD: ${result.get('eth_usd_value', 0.0):.2f}")
        print(f"Source: {result.get('source', 'unknown')}")
    else:
        if 'error' in result:
            print(f"Error: {result['error']}")
    
    print("=" * 80)
    
    # Exit with error code if auth failed
    if result['status'] == 'AUTH_FAIL':
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
