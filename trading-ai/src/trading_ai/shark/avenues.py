"""Avenue registry — one master agent, multiple revenue + intelligence avenues, one treasury."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def avenues_path() -> Path:
    return shark_state_path("avenues.json")


@dataclass
class Avenue:
    name: str
    platform: str
    avenue_type: str          # prediction_market | options | sports_betting
    starting_capital: float
    automation_level: str     # full | semi | manual_only
    # All targets are MINIMUMS — faster is always better
    month_1_target: float
    month_2_target: float
    month_3_target: float
    month_4_target: float
    month_5_target: float
    month_6_target: float
    year_end_target: float
    current_capital: float = 0.0
    total_profit: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    status: str = "active"    # active | paused | suspended
    last_updated: str = ""

    def __post_init__(self) -> None:
        # On first creation current_capital equals starting_capital
        if self.current_capital == 0.0:
            self.current_capital = self.starting_capital
        if not self.last_updated:
            self.last_updated = _iso()


# ── Default registry ─────────────────────────────────────────────────────────

def _default_avenues() -> Dict[str, Avenue]:
    # All targets are MINIMUMS across all avenues — faster is always better
    _t = dict(
        month_1_target=1_750.00,
        month_2_target=8_000.00,
        month_3_target=35_000.00,
        month_4_target=120_000.00,
        month_5_target=480_000.00,
        month_6_target=780_000.00,
        year_end_target=1_200_000.00,
    )
    return {
        "kalshi": Avenue(
            name="Kalshi",
            platform="kalshi",
            avenue_type="prediction_market",
            starting_capital=25.00,
            automation_level="full",
            **_t,
        ),
        "manifold": Avenue(
            name="Manifold",
            platform="manifold",
            avenue_type="prediction_market",
            # Note: Manifold balance is mana (play money); tracked separately.
            starting_capital=25.00,
            automation_level="full",
            **_t,
        ),
        "polymarket": Avenue(
            name="Polymarket",
            platform="polymarket",
            avenue_type="prediction_market",
            starting_capital=25.00,
            automation_level="full",
            **_t,
        ),
        "metaculus": Avenue(
            name="Metaculus",
            platform="metaculus",
            avenue_type="prediction_market",
            starting_capital=25.00,
            automation_level="manual_only",
            **_t,
        ),
        "coinbase": Avenue(
            name="Coinbase",
            platform="coinbase",
            avenue_type="crypto",
            starting_capital=25.00,
            automation_level="semi",
            **_t,
        ),
        "robinhood": Avenue(
            name="Robinhood",
            platform="robinhood",
            avenue_type="equity",
            starting_capital=25.00,
            automation_level="semi",
            **_t,
        ),
        "tastytrade": Avenue(
            name="Tastytrade",
            platform="tastytrade",
            avenue_type="options",
            starting_capital=25.00,
            automation_level="semi",
            **_t,
        ),
        "webull": Avenue(
            name="Webull",
            platform="webull",
            avenue_type="options",
            starting_capital=25.00,
            automation_level="semi",
            **_t,
        ),
        "sports_manual": Avenue(
            name="Sports Betting",
            platform="fanduel_draftkings",
            avenue_type="sports_betting",
            starting_capital=25.00,
            # NY law prohibits automated sports betting execution
            automation_level="manual_only",
            **_t,
        ),
    }


# ── Persistence ───────────────────────────────────────────────────────────────

def _avenue_to_dict(a: Avenue) -> Dict[str, Any]:
    return asdict(a)


def _avenue_from_dict(d: Dict[str, Any]) -> Avenue:
    valid = {f.name for f in Avenue.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return Avenue(**{k: v for k, v in d.items() if k in valid})


def load_avenues() -> Dict[str, Avenue]:
    """Load avenue state from disk; fills in missing avenues from defaults."""
    defaults = _default_avenues()
    p = avenues_path()
    if not p.is_file():
        return defaults
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return defaults
        result: Dict[str, Avenue] = {}
        for key, default in defaults.items():
            if key in raw and isinstance(raw[key], dict):
                stored = dict(asdict(default))
                stored.update(raw[key])
                result[key] = _avenue_from_dict(stored)
            else:
                result[key] = default
        return result
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults


def save_avenues(avenues: Dict[str, Avenue]) -> None:
    payload = {k: _avenue_to_dict(v) for k, v in avenues.items()}
    avenues_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ── Mutations ─────────────────────────────────────────────────────────────────

def set_avenue_status(avenue_name: str, status: str) -> None:
    """Set ``status`` (active|paused|suspended). Triggers strategy unlock when transitioning to active."""
    avenues = load_avenues()
    if avenue_name not in avenues:
        return
    prev = (avenues[avenue_name].status or "").lower()
    avenues[avenue_name].status = status
    avenues[avenue_name].last_updated = _iso()
    save_avenues(avenues)
    new = (status or "").lower()
    if prev != "active" and new == "active":
        try:
            from trading_ai.shark import avenue_activator

            avenue_activator.on_avenue_became_active(avenue_name, previous_status=prev)
        except Exception as exc:
            logger.warning("avenue activator hook failed: %s", exc)


def update_avenue_capital(avenue_name: str, new_capital: float) -> None:
    """Set current_capital for an avenue and persist."""
    avenues = load_avenues()
    if avenue_name not in avenues:
        return
    avenues[avenue_name].current_capital = round(new_capital, 2)
    avenues[avenue_name].last_updated = _iso()
    save_avenues(avenues)


def record_trade_result(avenue_name: str, pnl: float, win: bool) -> None:
    """
    Update avenue P&L after a trade resolves.
    Recalculates win_rate with a simple running average.
    """
    avenues = load_avenues()
    if avenue_name not in avenues:
        return
    av = avenues[avenue_name]
    av.current_capital = round(max(0.0, av.current_capital + pnl), 2)
    av.total_profit = round(av.total_profit + pnl, 2)
    av.total_trades += 1
    wins = round(av.win_rate * (av.total_trades - 1)) + (1 if win else 0)
    av.win_rate = round(wins / av.total_trades, 4)
    av.last_updated = _iso()
    save_avenues(avenues)


def get_avenue_summary() -> Dict[str, Any]:
    """Return per-avenue state plus aggregate totals."""
    avenues = load_avenues()
    total_deployed = sum(a.starting_capital for a in avenues.values())
    total_current = sum(a.current_capital for a in avenues.values())
    total_profit = sum(a.total_profit for a in avenues.values())
    return {
        "total_capital_deployed": round(total_deployed, 2),
        "total_current_value": round(total_current, 2),
        "total_profit": round(total_profit, 2),
        "avenues": {k: _avenue_to_dict(v) for k, v in avenues.items()},
    }
