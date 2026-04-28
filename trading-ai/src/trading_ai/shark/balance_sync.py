"""Fetch live balances from Kalshi and Manifold; treasury + capital.json.

Kalshi: ``sync_all_platforms`` runs on Shark startup and every 5 minutes (see ``run_shark`` /
``build_shark_scheduler`` job ``balance_sync``), and after scans where applicable.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv
from trading_ai.shark.kalshi_limits import kalshi_open_positions_deployed_usd

load_shark_dotenv()

logger = logging.getLogger(__name__)


def kalshi_balance_trust_min_usd() -> float:
    """Balances above this from the API are treated as the high-confidence band (default $1)."""
    raw = (os.environ.get("KALSHI_BALANCE_TRUST_MIN_USD") or "1.0").strip() or "1.0"
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


def kalshi_api_reports_zero_balance(x: float) -> bool:
    """True when the API reports exactly ~$0 (known bug case — optional KALSHI_ACTUAL_BALANCE)."""
    return abs(float(x)) <= 1e-9


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_kalshi_balance_usd() -> Optional[float]:
    """
    GET /portfolio/balance → available_balance (cents) → USD.
    Returns None if auth unavailable or request fails.
    """
    try:
        from trading_ai.shark.outlets.kalshi import KalshiClient

        client = KalshiClient()
        if not client.uses_rsa_auth():
            logger.debug("Kalshi: no RSA auth configured; skipping balance fetch")
            return None
        url = client.base_url + "/portfolio/balance"
        headers = client._auth_headers("GET", url)
        headers["User-Agent"] = "EzrasTreasury/1.0"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        # API returns 'balance' in cents, not 'available_balance'
        cents = body.get("balance", 0)
        return round(float(cents) / 100, 2)
    except urllib.error.HTTPError as e:
        logger.warning("Kalshi balance fetch HTTP %s: %s", e.code, e.reason)
        return None
    except Exception as exc:
        logger.warning("Kalshi balance fetch error: %s", exc)
        return None


def fetch_manifold_balance_mana() -> Optional[float]:
    """
    GET /v0/me → balance in mana (play money unless real-money markets enabled).
    Returns None if credentials missing or request fails.
    """
    try:
        api_key = (os.environ.get("MANIFOLD_API_KEY") or "").strip()
        if not api_key:
            return None
        base = (os.environ.get("MANIFOLD_API_BASE") or "https://api.manifold.markets/v0").rstrip("/")
        url = f"{base}/me"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Key {api_key}",
                "User-Agent": "EzrasTreasury/1.0",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        return round(float(body.get("balance", 0)), 2)
    except Exception as exc:
        logger.warning("Manifold balance fetch error: %s", exc)
        return None


def sync_all_platforms() -> Dict:
    """
    Fetch balances from all platforms and update treasury.
    Falls back to last-known value when a fetch fails.
    Returns sync result dict.
    """
    from trading_ai.shark.outlets import polymarket_enabled
    from trading_ai.shark.treasury import load_treasury, update_platform_balances

    existing = load_treasury()
    kalshi_fetched = fetch_kalshi_balance_usd()
    manifold_fetched = fetch_manifold_balance_mana()

    poly_fetched = None
    if polymarket_enabled():
        try:
            from trading_ai.shark.outlets.polymarket import fetch_polymarket_balance

            poly_fetched = fetch_polymarket_balance()
        except Exception as exc:
            logger.warning("Polymarket balance sync skipped: %s", exc)
            poly_fetched = None

    try:
        env_k = float((os.environ.get("KALSHI_ACTUAL_BALANCE") or "0").strip() or 0)
    except ValueError:
        env_k = 0.0

    trust_min = kalshi_balance_trust_min_usd()
    # Portfolio API drives treasury: trust balances > trust_min; exact $0 + env → override;
    # small non-zero API amounts still sync from API; fetch failure → last known.
    if kalshi_fetched is not None and kalshi_fetched > trust_min:
        kalshi_final = float(kalshi_fetched)
        logger.info("Kalshi cash synced: $%.2f (from API)", kalshi_final)
    elif kalshi_fetched is not None and kalshi_api_reports_zero_balance(kalshi_fetched) and env_k > 1e-6:
        kalshi_final = env_k
        logger.info("Kalshi cash: using override $%.2f (API returned $0)", env_k)
    elif kalshi_fetched is not None:
        kalshi_final = float(kalshi_fetched)
        logger.info("Kalshi cash synced: $%.2f (from API)", kalshi_final)
    else:
        kalshi_final = float(existing.get("kalshi_balance_usd", 0.0))
        logger.debug(
            "Kalshi balance API unavailable; treasury kalshi unchanged at $%.2f",
            kalshi_final,
        )
    if manifold_fetched is not None:
        manifold_mana_final = manifold_fetched
    else:
        manifold_mana_final = float(existing.get("manifold_mana_balance", existing.get("manifold_balance_usd", 0.0)) or 0.0)

    poly_final = poly_fetched if poly_fetched is not None else float(existing.get("polymarket_balance_usd", 0.0))

    rm = (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower() in ("1", "true", "yes")
    manifold_usd_final = round(float(manifold_mana_final), 2) if rm else 0.0

    update_platform_balances(kalshi_final, manifold_usd_final, manifold_mana_final, poly_final)

    st_after = load_treasury()
    net = float(st_after.get("net_worth_usd", 0.0))
    try:
        from trading_ai.shark.state_store import load_capital, save_capital

        rec = load_capital()
        rec.current_capital = net
        if rec.peak_capital < net:
            rec.peak_capital = net
        save_capital(rec)
    except Exception as exc:
        logger.warning("capital.json mirror after balance sync failed: %s", exc)

    deployed_k = kalshi_open_positions_deployed_usd()
    if kalshi_fetched is not None:
        logger.info(
            "Kalshi liquidity: api_available_cash=$%.2f positions_deployed_usd=$%.2f treasury_kalshi_book=$%.2f",
            kalshi_fetched,
            deployed_k,
            kalshi_final,
        )
    else:
        logger.info(
            "Kalshi liquidity: api_available_cash=n/a positions_deployed_usd=$%.2f treasury_kalshi_book=$%.2f (API fetch failed; last known)",
            deployed_k,
            kalshi_final,
        )

    result = {
        "synced_at": _iso(),
        "kalshi_usd": kalshi_final,
        "kalshi_fetched": kalshi_fetched is not None,
        "polymarket_usd": poly_final,
        "polymarket_fetched": poly_fetched is not None,
        "manifold_mana": manifold_mana_final,
        "manifold_fetched": manifold_fetched is not None,
        "net_worth_usd": net,
    }
    logger.info(
        "balance sync: kalshi=$%.2f poly=$%.2f manifold_mana=%.0f net_usd=$%.2f (capital.json updated)",
        kalshi_final,
        poly_final,
        manifold_mana_final,
        result["net_worth_usd"],
    )
    try:
        from trading_ai.shark.master_wallet import sync_master_wallet_from_runtime

        sync_master_wallet_from_runtime(result)
    except Exception as exc:
        logger.debug("master_wallet sync skipped: %s", exc)
    return result
