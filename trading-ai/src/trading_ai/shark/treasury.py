"""Master wallet + platform balance tracking. No automatic fund movement."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

from trading_ai.shark import reporting as _reporting


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def treasury_path() -> Path:
    return shark_state_path("treasury.json")


_DEFAULTS: Dict[str, Any] = {
    "master_wallet_address": "",
    "total_deposited_usd": 10.00,
    "total_profit_usd": 0.00,
    "total_withdrawn_usd": 0.00,
    "net_worth_usd": 10.00,
    "kalshi_balance_usd": 10.00,
    "manifold_mana_balance": 0.00,
    "manifold_usd_balance": 0.00,
    "manifold_balance_usd": 0.00,
    "usdc_target_pct": 60,
    "eth_target_pct": 40,
    "withdrawal_alert_threshold": 5000.00,
    "last_updated": "",
    "withdrawal_history": [],
}


def _manifold_usd_from_mana(mana: float) -> float:
    """Manifold balance is mana (play money). USD component only if MANIFOLD_REAL_MONEY=true."""
    v = (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return round(float(mana), 2)
    return 0.0


def _apply_env_overrides(state: Dict[str, Any]) -> None:
    """Overlay env-var values into state dict (non-destructive if env is unset)."""
    addr = (os.environ.get("MASTER_WALLET_ADDRESS") or "").strip()
    if addr:
        state["master_wallet_address"] = addr
    try:
        state["usdc_target_pct"] = int(os.environ["MASTER_WALLET_USDC_TARGET_PCT"])
    except (KeyError, ValueError, TypeError):
        pass
    try:
        state["eth_target_pct"] = int(os.environ["MASTER_WALLET_ETH_TARGET_PCT"])
    except (KeyError, ValueError, TypeError):
        pass
    try:
        state["withdrawal_alert_threshold"] = float(os.environ["WITHDRAWAL_ALERT_THRESHOLD"])
    except (KeyError, ValueError, TypeError):
        pass


def load_treasury() -> Dict[str, Any]:
    p = treasury_path()
    if not p.is_file():
        state = dict(_DEFAULTS)
        state["last_updated"] = _iso()
        _apply_env_overrides(state)
        return state
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not a dict")
        if "manifold_mana_balance" not in raw:
            raw["manifold_mana_balance"] = float(raw.get("manifold_balance_usd", 0) or 0)
        for k, v in _DEFAULTS.items():
            raw.setdefault(k, v)
        mana = float(raw.get("manifold_mana_balance", 0) or 0)
        raw["manifold_usd_balance"] = _manifold_usd_from_mana(mana)
        raw["manifold_balance_usd"] = raw["manifold_usd_balance"]
        kbal = float(raw.get("kalshi_balance_usd", 0) or 0)
        raw["net_worth_usd"] = round(kbal + raw["manifold_usd_balance"], 2)
        _apply_env_overrides(raw)
        return raw
    except (OSError, json.JSONDecodeError, ValueError):
        state = dict(_DEFAULTS)
        state["last_updated"] = _iso()
        _apply_env_overrides(state)
        return state


def save_treasury(state: Dict[str, Any]) -> None:
    state["last_updated"] = _iso()
    treasury_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _manifold_real_money_enabled() -> bool:
    v = (os.environ.get("MANIFOLD_REAL_MONEY") or "").strip().lower()
    return v in ("1", "true", "yes")


def update_platform_balances(
    kalshi_usd: float,
    manifold_usd: float,
    manifold_mana: float = 0.0,
) -> None:
    """Kalshi is USD. Manifold balance is mana (play money); USD fields are 0 unless MANIFOLD_REAL_MONEY=true."""
    state = load_treasury()
    state["kalshi_balance_usd"] = round(kalshi_usd, 2)
    state["manifold_mana_balance"] = round(float(manifold_mana), 2)
    musd = round(float(manifold_usd), 2) if _manifold_real_money_enabled() else 0.0
    state["manifold_usd_balance"] = musd
    state["manifold_balance_usd"] = musd
    state["net_worth_usd"] = round(kalshi_usd + musd, 2)
    deposited = state.get("total_deposited_usd", 10.0)
    withdrawn = state.get("total_withdrawn_usd", 0.0)
    state["total_profit_usd"] = round(state["net_worth_usd"] - deposited + withdrawn, 2)
    save_treasury(state)
    check_withdrawal_alert()


def check_withdrawal_alert() -> bool:
    """Return True if Kalshi USD (real trading capital) exceeds threshold. Never based on Manifold mana."""
    state = load_treasury()
    net = state.get("net_worth_usd", 0.0)
    threshold = state.get("withdrawal_alert_threshold", 5000.0)
    kalshi = float(state.get("kalshi_balance_usd", 0.0))
    if kalshi <= threshold:
        return False

    addr = state.get("master_wallet_address") or "(not set)"
    usdc_pct = state.get("usdc_target_pct", 60)
    eth_pct = state.get("eth_target_pct", 40)
    musd = float(state.get("manifold_usd_balance", 0.0) or 0.0)
    lines = [
        "💰 WITHDRAWAL ALERT",
        f"Kalshi: ${kalshi:.2f} — exceeds threshold ${threshold:.2f}",
        f"Treasury net: ${net:.2f}",
    ]
    if _manifold_real_money_enabled() and musd > 0:
        lines.append(f"Manifold (USD): ${musd:.2f}")
    lines.extend(
        [
            "",
            "Action required:",
            "1. Log into Kalshi",
            "2. Withdraw profits",
            f"3. Send to MetaMask: {addr}",
            f"4. Split: {usdc_pct}% USDC / {eth_pct}% ETH",
            "",
            "Run: python -m trading_ai shark treasury confirm-withdrawal"
            f" --amount {net:.2f}",
        ]
    )
    msg = "\n".join(lines)
    try:
        _reporting.send_telegram(msg)
    except Exception:
        pass
    return True


def log_withdrawal(amount_usd: float) -> None:
    """Record a manual withdrawal. Updates totals and reduces net worth."""
    state = load_treasury()
    entry: Dict[str, Any] = {
        "amount_usd": round(amount_usd, 2),
        "timestamp": _iso(),
        "kalshi_balance_at_time": state.get("kalshi_balance_usd", 0.0),
        "manifold_balance_at_time": state.get("manifold_balance_usd", 0.0),
        "manifold_mana_at_time": state.get("manifold_mana_balance", 0.0),
    }
    history: List[Any] = state.get("withdrawal_history", [])
    history.append(entry)
    state["withdrawal_history"] = history
    state["total_withdrawn_usd"] = round(state.get("total_withdrawn_usd", 0.0) + amount_usd, 2)
    state["net_worth_usd"] = round(max(0.0, state.get("net_worth_usd", 0.0) - amount_usd), 2)
    save_treasury(state)


def get_treasury_summary() -> Dict[str, Any]:
    """Return full treasury state with computed all-time profit and ROI."""
    state = load_treasury()
    deposited = state.get("total_deposited_usd", 10.0)
    withdrawn = state.get("total_withdrawn_usd", 0.0)
    net = state.get("net_worth_usd", 0.0)
    all_time_profit = round(net + withdrawn - deposited, 2)
    roi_pct = round((all_time_profit / max(deposited, 1e-9)) * 100, 2)
    return {
        **state,
        "all_time_profit_usd": all_time_profit,
        "return_on_investment_pct": roi_pct,
    }
