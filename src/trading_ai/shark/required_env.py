"""Strict `EnvironmentError` messages for live execution and daemon entry points."""

from __future__ import annotations

import os

from trading_ai.shark.dotenv_load import load_shark_dotenv


def _missing_msg(name: str) -> str:
    return (
        f"Missing required env var: {name} — "
        "add it to your .env file (see .env.template)"
    )


def require_poly_wallet_key() -> str:
    load_shark_dotenv()
    v = (os.environ.get("POLY_WALLET_KEY") or "").strip()
    if not v:
        raise EnvironmentError(_missing_msg("POLY_WALLET_KEY"))
    return v


def require_poly_api_key() -> str:
    load_shark_dotenv()
    v = (os.environ.get("POLY_API_KEY") or "").strip()
    if not v:
        raise EnvironmentError(_missing_msg("POLY_API_KEY"))
    return v


def require_polymarket_live_keys() -> None:
    require_poly_wallet_key()
    require_poly_api_key()


def require_kalshi_api_key() -> str:
    load_shark_dotenv()
    raw = os.environ.get("KALSHI_API_KEY") or ""
    from trading_ai.shark.outlets.kalshi import is_kalshi_pem_private_key, normalize_kalshi_key_material

    v = normalize_kalshi_key_material(raw).strip()
    if not v:
        raise EnvironmentError(_missing_msg("KALSHI_API_KEY"))
    if is_kalshi_pem_private_key(v):
        if not (os.environ.get("KALSHI_ACCESS_KEY_ID") or "").strip():
            raise EnvironmentError(_missing_msg("KALSHI_ACCESS_KEY_ID"))
    return v


def require_manifold_api_key() -> str:
    load_shark_dotenv()
    v = (os.environ.get("MANIFOLD_API_KEY") or "").strip()
    if not v:
        raise EnvironmentError(_missing_msg("MANIFOLD_API_KEY"))
    return v


def require_telegram_credentials() -> tuple[str, str]:
    load_shark_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise EnvironmentError(_missing_msg("TELEGRAM_BOT_TOKEN"))
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        raise EnvironmentError(_missing_msg("TELEGRAM_CHAT_ID"))
    return token, chat


def require_ezras_runtime_root() -> None:
    load_shark_dotenv()
    if not (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip():
        raise EnvironmentError(_missing_msg("EZRAS_RUNTIME_ROOT"))
