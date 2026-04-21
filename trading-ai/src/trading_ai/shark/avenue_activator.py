"""Hooks when avenues become active — auto-unlock master strategies + credential pulse (Beast)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def activation_snapshot_path() -> Path:
    return shark_state_path("avenue_activation_snapshot.json")


@dataclass
class AvenueRuntimeStatus:
    key: str
    label: str
    state: str
    detail: str


def _kalshi_ready() -> Tuple[bool, str]:
    return (bool((os.environ.get("KALSHI_API_KEY") or "").strip()), "KALSHI_API_KEY")


def _manifold_ready() -> Tuple[bool, str]:
    return (bool((os.environ.get("MANIFOLD_API_KEY") or "").strip()), "MANIFOLD_API_KEY")


def _metaculus_ready() -> Tuple[bool, str]:
    return (bool((os.environ.get("METACULUS_API_TOKEN") or "").strip()), "METACULUS_API_TOKEN")


def _coinbase_ready() -> Tuple[bool, str]:
    k = (os.environ.get("COINBASE_API_KEY") or "").strip()
    s = (os.environ.get("COINBASE_API_SECRET") or "").strip()
    return (bool(k and s), "COINBASE_API_KEY+SECRET")


def _robinhood_ready() -> Tuple[bool, str]:
    u = (os.environ.get("ROBINHOOD_USERNAME") or "").strip()
    p = (os.environ.get("ROBINHOOD_PASSWORD") or "").strip()
    return (bool(u and p), "ROBINHOOD_USERNAME+PASSWORD")


def _tastytrade_ready() -> Tuple[bool, str]:
    u = (os.environ.get("TASTYTRADE_USERNAME") or "").strip()
    p = (os.environ.get("TASTYTRADE_PASSWORD") or "").strip()
    return (bool(u and p), "TASTYTRADE_USERNAME+PASSWORD")


def evaluate_avenues() -> List[AvenueRuntimeStatus]:
    """Credential-based READY/PENDING (orthogonal to ``avenues.json`` trading status)."""
    rows: List[AvenueRuntimeStatus] = []
    defs = [
        ("kalshi", "Kalshi", _kalshi_ready),
        ("manifold", "Manifold", _manifold_ready),
        ("metaculus", "Metaculus", _metaculus_ready),
        ("coinbase", "Coinbase", _coinbase_ready),
        ("robinhood", "Robinhood", _robinhood_ready),
        ("tastytrade", "Tastytrade", _tastytrade_ready),
    ]
    for key, label, fn in defs:
        ok, detail = fn()
        st = "READY" if ok else "PENDING"
        if (os.environ.get(f"AVENUE_{key.upper()}_PAUSED") or "").strip().lower() in ("1", "true", "yes"):
            st = "PAUSED"
        if ok and key == "kalshi":
            try:
                from trading_ai.shark.balance_sync import fetch_kalshi_balance_usd

                bal = fetch_kalshi_balance_usd()
                if bal is not None and bal > 1.0:
                    st = "ACTIVE"
            except Exception:
                pass
        rows.append(AvenueRuntimeStatus(key=key, label=label, state=st, detail=detail))
    return rows


def _load_activation_snapshot() -> Dict[str, Any]:
    p = activation_snapshot_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_activation_snapshot(rows: List[AvenueRuntimeStatus]) -> None:
    p = activation_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {r.key: {"state": r.state, "detail": r.detail, "updated": _iso()} for r in rows}
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def scan_and_alert_transitions() -> List[str]:
    """Log when a venue moves PENDING→READY/ACTIVE (deduped via snapshot file)."""
    rows = evaluate_avenues()
    prev = _load_activation_snapshot()
    alerts: List[str] = []
    for r in rows:
        old = (prev.get(r.key) or {}).get("state")
        if old == r.state:
            continue
        if r.state in ("ACTIVE", "READY") and old in (None, "", "PENDING", "ERROR"):
            msg = f"✅ [{r.label}] activated — {r.state} ({r.detail})"
            alerts.append(msg)
            logger.info("avenue transition (Telegram disabled): %s", msg)
    _save_activation_snapshot(rows)
    return alerts


def format_avenue_status_for_ceo() -> str:
    """Human-readable avenue lines for CEO prompts (Beast / six-avenue block)."""
    from trading_ai.shark.avenues import load_avenues

    avenues = load_avenues()
    lines: List[str] = []
    for key, a in sorted(avenues.items()):
        lines.append(
            f"- {key}: status={a.status} | capital=${float(a.current_capital):.2f} | "
            f"automation={a.automation_level} | type={a.avenue_type}"
        )
    return "\n".join(lines) if lines else "(no avenues loaded)"


def on_avenue_became_active(avenue_name: str, previous_status: str) -> None:
    """
    When an avenue transitions to ``active``, re-run capital-based strategy unlocks
    and notify if new strategies were enabled.
    """
    from trading_ai.shark.avenues import load_avenues
    from trading_ai.shark.master_strategies import STRATEGY_REGISTRY, auto_activate_strategies

    avenues = load_avenues()
    capital = sum(float(a.current_capital) for a in avenues.values())
    active_names: List[str] = [
        k for k, a in avenues.items() if (a.status or "").lower() == "active"
    ]
    activated = auto_activate_strategies(capital, active_names)
    if not activated:
        logger.info("Avenue %s active (was %s); no new strategies unlocked", avenue_name, previous_status)
        return
    labels = [STRATEGY_REGISTRY[sid].name for sid in activated]
    msg = (
        "🚀 New strategies unlocked:\n"
        + "\n".join(f"  • {n}" for n in labels)
        + "\nCapital threshold reached!"
    )
    logger.info("strategy unlock (Telegram disabled): %s", msg.replace("\n", " | "))
    logger.info(
        "Avenue %s became active (was %s); unlocked strategies: %s",
        avenue_name,
        previous_status,
        [s.value for s in activated],
    )
