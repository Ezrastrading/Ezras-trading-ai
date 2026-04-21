"""
Supabase trade logger — logs every trade for
AI learning and performance tracking.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, key_src = resolve_supabase_jwt_key()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        _client = create_client(url, key)
        host = urlparse(url).netloc or "unknown"
        logger.info("Supabase connected host=%s key_source=%s", host, key_src)
        return _client
    except Exception as e:
        logger.warning("Supabase connect failed: %s", e)
        return None


def log_trade(
    platform: str,  # 'coinbase' or 'kalshi'
    gate: str,  # 'A', 'B', 'C', 'D'
    product_id: str,  # 'BTC-USD' or 'KXBTC-...'
    side: str,  # 'buy' or 'sell'
    strategy: str,  # 'mom', 'dip', 'gainer'
    entry_price: float,
    exit_price: float,
    size_usd: float,
    pnl_usd: float,
    exit_reason: str,  # 'profit', 'stop', 'timeout'
    hold_seconds: int,
    balance_after: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        pnl_pct = ((exit_price - entry_price) / entry_price) if entry_price else 0
        win = pnl_usd >= 0
        data = {
            "platform": platform,
            "gate": gate,
            "product_id": product_id,
            "side": side,
            "strategy": strategy,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size_usd": size_usd,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "hold_seconds": hold_seconds,
            "win": win,
            "balance_after": balance_after,
            "metadata": metadata or {},
        }
        client.table("trades").insert(data).execute()
        logger.info(
            "Supabase logged: %s %s %s pnl=$%.4f",
            platform,
            product_id,
            exit_reason,
            pnl_usd,
        )
        try:
            from trading_ai.shark.progression import record_trade

            record_trade(
                platform=platform,
                gate=gate,
                product_id=product_id,
                pnl_usd=pnl_usd,
                exit_reason=exit_reason,
                hold_seconds=hold_seconds,
                balance_after=balance_after,
                win=win,
            )
        except Exception:
            pass
        return True
    except Exception as e:
        logger.warning("Supabase log_trade failed: %s", e)
        return False


def log_performance(
    platform: str,
    trades_count: int,
    wins: int,
    losses: int,
    profit_usd: float,
    balance_usd: float,
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        win_rate = wins / trades_count if trades_count else 0
        data = {
            "platform": platform,
            "hour_start": time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime()),
            "trades_count": trades_count,
            "wins": wins,
            "losses": losses,
            "profit_usd": profit_usd,
            "win_rate": win_rate,
            "balance_usd": balance_usd,
        }
        client.table("performance").insert(data).execute()
        return True
    except Exception as e:
        logger.warning("Supabase log_performance: %s", e)
        return False


def log_ai_insight(
    insight_type: str,
    platform: str,
    gate: str,
    observation: str,
    recommendation: str,
) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        data = {
            "insight_type": insight_type,
            "platform": platform,
            "gate": gate,
            "observation": observation,
            "recommendation": recommendation,
            "applied": False,
        }
        client.table("ai_insights").insert(data).execute()
        return True
    except Exception as e:
        logger.warning("Supabase log_insight: %s", e)
        return False


def get_recent_trades(
    platform: Optional[str] = None,
    gate: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    client = _get_client()
    if not client:
        return []
    try:
        query = client.table("trades").select("*").order("created_at", desc=True).limit(limit)
        if platform:
            query = query.eq("platform", platform)
        if gate:
            query = query.eq("gate", gate)
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.warning("Supabase get_trades: %s", e)
        return []


def get_win_rate(platform: Optional[str] = None) -> dict:
    trades = get_recent_trades(platform=platform, limit=1000)
    if not trades:
        return {"win_rate": 0, "total": 0, "wins": 0, "losses": 0, "pnl": 0}
    wins = sum(1 for t in trades if t.get("win"))
    pnl = sum(t.get("pnl_usd", 0) for t in trades)
    return {
        "win_rate": wins / len(trades),
        "total": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "pnl": pnl,
    }

