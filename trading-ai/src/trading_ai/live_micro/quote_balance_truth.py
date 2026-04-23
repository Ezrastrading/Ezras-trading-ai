"""Coinbase quote balance truth for live_micro (USD + USDC).

Goal: prevent live_micro from blocking on missing_quote_balance_truth by persisting a durable,
fresh snapshot produced by the OPS supervisor before candidate execution runs.

This module is additive: it does not bypass any live guards and does not place orders.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_balance_truth_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "coinbase_quote_balance_truth.json"


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_quote_balance_truth(runtime_root: Path) -> Dict[str, Any]:
    p = quote_balance_truth_path(runtime_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_and_persist_quote_balances(
    *,
    runtime_root: Path,
    client: Any,
) -> Dict[str, Any]:
    """
    Writes a durable snapshot with spendable USD + USDC.

    Best-effort sources (in order):
    1) Paginated /accounts extraction (most accurate for USD + USDC split).
    2) Per-currency get_available_balance("USD"/"USDC") if available.
    3) Aggregate get_usd_balance() as USD-only fallback (honestly marked).
    """
    root = Path(runtime_root).resolve()
    out: Dict[str, Any] = {
        "truth_version": "coinbase_quote_balance_truth_v1",
        "generated_at_utc": _iso(),
        "generated_at_unix": time.time(),
        "ok": False,
        "balances": {"USD": 0.0, "USDC": 0.0},
        "source": None,
        "errors": [],
        "honesty": "Snapshot is best-effort; if split cannot be derived, aggregate fallback is explicitly marked.",
    }

    # Safe auth presence diagnostics (no secrets).
    try:
        key_name = ""
        if hasattr(client, "resolved_key_name"):
            key_name = str(client.resolved_key_name() or "").strip()
        else:
            key_name = str(getattr(client, "_key_name", "") or "").strip()
        has_key = bool(key_name)
    except Exception:
        key_name = ""
        has_key = False
    try:
        # CoinbaseClient defines has_credentials(); if absent, fall back to env check.
        has_creds = bool(getattr(client, "has_credentials", lambda: False)())
    except Exception:
        has_creds = False
    logger.info(
        "live_micro_quote_balance_truth: start (has_credentials=%s has_key_name=%s key_name_org_prefix=%s)",
        bool(has_creds),
        bool(has_key),
        bool((key_name or "").startswith("organizations/")),
    )

    # Source 1: runtime_proof helper (uses /accounts pagination).
    try:
        from trading_ai.runtime_proof.coinbase_accounts import get_available_quote_balances

        bal = get_available_quote_balances(client)
        if isinstance(bal, dict):
            out["balances"] = {"USD": _f(bal.get("USD")), "USDC": _f(bal.get("USDC"))}
            out["source"] = "runtime_proof.coinbase_accounts:get_available_quote_balances"
            logger.info(
                "live_micro_quote_balance_truth: accounts balances USD=%.6f USDC=%.6f",
                float(out["balances"]["USD"]),
                float(out["balances"]["USDC"]),
            )
    except Exception as exc:
        out["errors"].append(f"accounts_pagination_failed:{type(exc).__name__}")
        logger.warning("live_micro_quote_balance_truth: accounts pagination failed: %s", type(exc).__name__)

    # Source 2: per-currency getters.
    if float(out["balances"].get("USD") or 0.0) <= 0.0 and float(out["balances"].get("USDC") or 0.0) <= 0.0:
        try:
            usd = 0.0
            usdc = 0.0
            try:
                usd = _f(client.get_available_balance("USD"))
            except Exception:
                usd = 0.0
            try:
                usdc = _f(client.get_available_balance("USDC"))
            except Exception:
                usdc = 0.0
            if usd > 0.0 or usdc > 0.0:
                out["balances"] = {"USD": usd, "USDC": usdc}
                out["source"] = "CoinbaseClient.get_available_balance"
                logger.info(
                    "live_micro_quote_balance_truth: per-currency balances USD=%.6f USDC=%.6f",
                    float(out["balances"]["USD"]),
                    float(out["balances"]["USDC"]),
                )
        except Exception as exc:
            out["errors"].append(f"per_currency_failed:{type(exc).__name__}")
            logger.warning("live_micro_quote_balance_truth: per-currency fetch failed: %s", type(exc).__name__)

    # Source 3: aggregate fallback.
    if float(out["balances"].get("USD") or 0.0) <= 0.0 and float(out["balances"].get("USDC") or 0.0) <= 0.0:
        try:
            total = _f(client.get_usd_balance())
            if total > 0.0:
                out["balances"] = {"USD": total, "USDC": 0.0}
                out["source"] = "CoinbaseClient.get_usd_balance(aggregate)"
                out["honesty"] = (
                    "Fallback used: USD includes aggregated USD+USDC spendable; USDC split unknown."
                )
                logger.info(
                    "live_micro_quote_balance_truth: aggregate balance USD(total)=%.6f",
                    float(out["balances"]["USD"]),
                )
        except Exception as exc:
            out["errors"].append(f"aggregate_failed:{type(exc).__name__}")
            logger.warning("live_micro_quote_balance_truth: aggregate fetch failed: %s", type(exc).__name__)

    out["ok"] = (float(out["balances"].get("USD") or 0.0) + float(out["balances"].get("USDC") or 0.0)) > 0.0
    p = quote_balance_truth_path(root)
    logger.info("live_micro_quote_balance_truth: writing artifact path=%s", str(p))
    try:
        _write_json_atomic(p, out)
        logger.info("live_micro_quote_balance_truth: write ok (ok=%s source=%s)", bool(out["ok"]), out.get("source"))
    except Exception as exc:
        logger.warning("live_micro_quote_balance_truth: write failed: %s", type(exc).__name__)
        raise
    return out


def required_quote_available(
    *,
    runtime_root: Path,
    quote_currency: str,
    max_age_sec: float = 30.0,
) -> Tuple[bool, float, Dict[str, Any]]:
    """
    Returns (ok, available_amount, snapshot).
    """
    snap = load_quote_balance_truth(runtime_root)
    q = str(quote_currency or "").strip().upper() or "USD"
    ts = _f(snap.get("generated_at_unix"))
    age = max(0.0, time.time() - ts) if ts > 0 else 1e9
    bal = snap.get("balances") if isinstance(snap.get("balances"), dict) else {}
    avail = _f(bal.get(q))
    if age > float(max_age_sec):
        return False, avail, {**snap, "stale": True, "age_sec": age, "missing_quote_currency": q}
    if avail <= 0.0:
        return False, avail, {**snap, "stale": False, "age_sec": age, "missing_quote_currency": q}
    return True, avail, {**snap, "stale": False, "age_sec": age}

