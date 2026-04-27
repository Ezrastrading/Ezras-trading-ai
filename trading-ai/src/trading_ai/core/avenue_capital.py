"""
Avenue-specific capital fetcher for multi-avenue system.

Each avenue (Coinbase, Kalshi, etc.) has its own capital pool.
No mixing between avenues.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def get_avenue_capital(avenue: str) -> Dict[str, Any]:
    """
    Get capital for a specific avenue.
    
    Returns dict with:
    - available_usd: float
    - capital_available: bool
    - source: str
    - error: Optional[str]
    
    Args:
        avenue: One of "coinbase", "kalshi", "polymarket", etc.
    """
    avenue_lower = avenue.lower().strip()
    
    if avenue_lower == "coinbase":
        return _get_coinbase_capital()
    elif avenue_lower == "kalshi":
        return _get_kalshi_capital()
    else:
        return {
            "available_usd": 0.0,
            "capital_available": False,
            "source": "unsupported_avenue",
            "error": f"Unsupported avenue: {avenue}",
        }


def _get_coinbase_capital() -> Dict[str, Any]:
    """Fetch Coinbase capital (USD + USDC)."""
    try:
        from trading_ai.shark.coinbase_tracker import get_coinbase_balance
        
        balance = get_coinbase_balance()
        usdc = float(balance.get("usdc", 0.0) or 0.0)
        eth_usd = float(balance.get("eth_usd_value", 0.0) or 0.0)
        total_usd = usdc + eth_usd
        
        # Check if auth failed
        source = balance.get("source", "")
        auth_failed = source in ("no_credentials", "fetch_failed", "http_error_401", "http_error_403")
        
        result = {
            "available_usd": total_usd,
            "capital_available": not auth_failed and total_usd > 0,
            "source": source,
            "usdc": usdc,
            "eth_usd_value": eth_usd,
            "error": None if not auth_failed else f"Coinbase auth failed: {source}",
        }
        
        logger.info("Capital (Coinbase): $%.2f [source=%s, available=%s]", total_usd, source, result["capital_available"])
        return result
        
    except Exception as exc:
        logger.warning("Coinbase capital fetch error: %s", exc)
        return {
            "available_usd": 0.0,
            "capital_available": False,
            "source": "fetch_error",
            "error": str(exc),
        }


def _get_kalshi_capital() -> Dict[str, Any]:
    """Fetch Kalshi capital from treasury."""
    try:
        from trading_ai.shark.treasury import load_treasury
        
        treasury = load_treasury()
        kalshi_usd = float(treasury.get("kalshi_balance_usd", 0.0) or 0.0)
        
        # Check environment override
        env_override = (os.environ.get("KALSHI_ACTUAL_BALANCE") or "").strip()
        if env_override:
            try:
                kalshi_usd = float(env_override)
            except ValueError:
                pass
        
        result = {
            "available_usd": kalshi_usd,
            "capital_available": kalshi_usd > 0,
            "source": "treasury",
            "error": None,
        }
        
        logger.info("Capital (Kalshi): $%.2f [source=%s, available=%s]", kalshi_usd, "treasury", result["capital_available"])
        return result
        
    except Exception as exc:
        logger.warning("Kalshi capital fetch error: %s", exc)
        return {
            "available_usd": 0.0,
            "capital_available": False,
            "source": "fetch_error",
            "error": str(exc),
        }


def get_all_avenue_capitals() -> Dict[str, Dict[str, Any]]:
    """Get capital for all known avenues."""
    return {
        "coinbase": get_avenue_capital("coinbase"),
        "kalshi": get_avenue_capital("kalshi"),
    }


def log_avenue_capitals() -> None:
    """Log all avenue capitals for visibility."""
    capitals = get_all_avenue_capitals()
    
    for avenue, cap in capitals.items():
        available = cap.get("available_usd", 0.0)
        available_flag = cap.get("capital_available", False)
        source = cap.get("source", "unknown")
        error = cap.get("error")
        
        if error:
            logger.warning("Capital (%s): $%.2f [available=%s, source=%s, error=%s]", avenue.capitalize(), available, available_flag, source, error)
        else:
            logger.info("Capital (%s): $%.2f [available=%s, source=%s]", avenue.capitalize(), available, available_flag, source)


def get_capital_for_trade(avenue: str) -> float:
    """
    Get available capital for a trade on a specific avenue.
    Returns 0.0 if capital is not available.
    
    Args:
        avenue: The avenue to trade on (e.g., "coinbase", "kalshi")
    
    Returns:
        Available USD capital for the avenue, or 0.0 if unavailable.
    """
    cap = get_avenue_capital(avenue)
    if cap.get("capital_available"):
        return float(cap.get("available_usd", 0.0))
    
    logger.warning("Capital not available for avenue %s: %s", avenue, cap.get("error", "Unknown error"))
    return 0.0
