"""
Polymarket wallet intelligence — scan profitable profiles, detect patterns, optional copy-trade tiering.

Never replaces the hunt engine: wallet signal is extra confidence only.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.shark.hunt_engine import run_hunts_on_market
from trading_ai.shark.models import HuntType, MarketSnapshot, OpportunityTier
from trading_ai.shark.state_store import load_wallets_registry, save_wallets_registry

logger = logging.getLogger(__name__)

PROFILES_URL = "https://data-api.polymarket.com/profiles"
MIN_SCORE_TO_TRACK = 0.60
TOP_N_DISPLAY = 3


@dataclass
class WalletProfileView:
    wallet_address: str
    total_profit: float = 0.0
    total_trades: int = 0
    win_rate_overall: float = 0.5
    profit_by_category: Dict[str, float] = field(default_factory=dict)
    trades_by_category: Dict[str, int] = field(default_factory=dict)
    win_rate_by_category: Dict[str, float] = field(default_factory=dict)
    average_entry_price: float = 0.5
    average_exit_price: float = 0.5
    active_positions: int = 0
    average_resolution_days_at_entry: float = 14.0
    raw: Dict[str, Any] = field(default_factory=dict)


def _float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def normalize_profile_row(row: Dict[str, Any]) -> WalletProfileView:
    """Map heterogeneous API payloads into a stable view."""
    addr = str(
        row.get("wallet_address")
        or row.get("address")
        or row.get("proxyWallet")
        or row.get("id")
        or ""
    )
    pbc: Dict[str, float] = {}
    tbc: Dict[str, int] = {}
    wbc: Dict[str, float] = {}
    cats = row.get("profitByCategory") or row.get("categories") or row.get("categoryStats") or []
    if isinstance(cats, list):
        for c in cats:
            if not isinstance(c, dict):
                continue
            name = str(c.get("category") or c.get("name") or "unknown").lower()
            pbc[name] = _float(c.get("profit") or c.get("totalProfit"))
            tbc[name] = _int(c.get("trades") or c.get("count"))
            wbc[name] = _float(c.get("winRate") or c.get("win_rate"), 0.5)
    elif isinstance(cats, dict):
        for k, v in cats.items():
            name = str(k).lower()
            if isinstance(v, dict):
                pbc[name] = _float(v.get("profit"))
                tbc[name] = _int(v.get("trades"))
                wbc[name] = _float(v.get("winRate"), 0.5)
            else:
                pbc[name] = _float(v)

    return WalletProfileView(
        wallet_address=addr,
        total_profit=_float(row.get("total_profit") or row.get("totalProfit") or row.get("pnl")),
        total_trades=_int(row.get("total_trades") or row.get("totalTrades") or row.get("trades")),
        win_rate_overall=_float(row.get("win_rate_overall") or row.get("winRate") or row.get("win_rate"), 0.5),
        profit_by_category=pbc,
        trades_by_category=tbc,
        win_rate_by_category=wbc,
        average_entry_price=_float(row.get("average_entry_price") or row.get("avgEntry") or row.get("averageEntryPrice"), 0.5),
        average_exit_price=_float(row.get("average_exit_price") or row.get("avgExit") or row.get("averageExitPrice"), 0.5),
        active_positions=_int(row.get("active_positions") or row.get("activePositions") or row.get("openPositions")),
        average_resolution_days_at_entry=_float(
            row.get("avg_resolution_days") or row.get("averageResolutionDays") or row.get("avgResolutionDaysAtEntry"), 14.0
        ),
        raw=row,
    )


def pattern_category_specialist(w: WalletProfileView) -> Optional[Tuple[str, float]]:
    """Pattern A: overall 45–60% win but one category >75% with >=20 trades."""
    wo = w.win_rate_overall
    if not (0.45 <= wo <= 0.60):
        return None
    best_cat = None
    best_wr = 0.0
    for cat, wr in w.win_rate_by_category.items():
        n = w.trades_by_category.get(cat, 0)
        if n >= 20 and wr > best_wr:
            best_wr = wr
            best_cat = cat
    if best_wr > 0.75 and best_cat:
        return best_cat, best_wr
    return None


def pattern_near_zero_accumulator(w: WalletProfileView) -> bool:
    """Pattern B: cheap entries, high exits, enough samples."""
    if w.total_trades < 10:
        return False
    return w.average_entry_price < 0.12 and w.average_exit_price > 0.70


def pattern_speed_arbitrageur(w: WalletProfileView) -> bool:
    """Pattern C: longer-dated entries, elevated win rate."""
    if w.win_rate_overall <= 0.65:
        return False
    if w.average_resolution_days_at_entry <= 7.0:
        return False
    return w.total_trades >= 15


def wallet_score(
    *,
    category_win_rate: float,
    profit_consistency: float,
    sample_size_confidence: float,
    recency_weight: float,
) -> float:
    return (
        category_win_rate * 0.40
        + profit_consistency * 0.30
        + sample_size_confidence * 0.20
        + recency_weight * 0.10
    )


def score_profile(w: WalletProfileView, pattern: str, specialist_category: Optional[str], cat_wr: float) -> float:
    profit_consistency = min(1.0, 0.45 + min(1.0, w.total_trades / 150.0) * 0.45)
    sample_size_confidence = min(1.0, w.total_trades / 200.0)
    recency_weight = 0.85
    cwr = cat_wr if specialist_category else w.win_rate_overall
    return wallet_score(
        category_win_rate=cwr,
        profit_consistency=profit_consistency,
        sample_size_confidence=sample_size_confidence,
        recency_weight=recency_weight,
    )


def fetch_polymarket_profiles(*, limit: int = 1000) -> List[Dict[str, Any]]:
    """GET Polymarket data-api profiles (best-effort parse)."""
    qs = urllib.parse.urlencode({"limit": min(limit, 1000), "sortBy": "profit"})
    url = f"{PROFILES_URL}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark-WalletIntel/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
        logger.warning("wallet profile fetch failed: %s", exc)
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for k in ("profiles", "data", "results"):
            v = raw.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def scan_and_update_registry(*, limit: int = 1000) -> Dict[str, Any]:
    """Fetch profiles, score, persist `wallets.json`, drop stale scores."""
    rows = fetch_polymarket_profiles(limit=limit)
    tracked: List[Dict[str, Any]] = []
    now = time.time()
    for row in rows:
        w = normalize_profile_row(row)
        if not w.wallet_address:
            continue
        spec = pattern_category_specialist(w)
        pattern = "none"
        specialist_category = None
        cat_wr = w.win_rate_overall
        if spec:
            pattern = "category_specialist"
            specialist_category, cat_wr = spec[0], spec[1]
        elif pattern_near_zero_accumulator(w):
            pattern = "near_zero_accumulator"
        elif pattern_speed_arbitrageur(w):
            pattern = "speed_arbitrageur"
        else:
            continue
        sc = score_profile(w, pattern, specialist_category, cat_wr if specialist_category else w.win_rate_overall)
        if sc < MIN_SCORE_TO_TRACK:
            continue
        tracked.append(
            {
                "address": w.wallet_address,
                "pattern": pattern,
                "specialist_category": specialist_category or "",
                "category_win_rate": round(cat_wr, 4),
                "score": round(sc, 4),
                "total_copies": 0,
                "copy_win_rate": None,
                "last_updated": now,
            }
        )
    tracked.sort(key=lambda x: x.get("score", 0), reverse=True)
    data = load_wallets_registry()
    data["tracked_wallets"] = tracked[:200]
    data["last_full_scan"] = now
    save_wallets_registry(data)
    return data


def evaluate_copy_trade_tier(market: MarketSnapshot, registry: Optional[Dict[str, Any]] = None) -> Optional[Tuple[OpportunityTier, bool]]:
    """
    Run full hunt engine first. If no hunt signals, no copy tier.
    If specialist wallet matches category / pattern, upgrade tier intent.
    Returns (tier, wallet_signal) or None.
    """
    registry = registry or load_wallets_registry()
    hunts = run_hunts_on_market(market)
    if not hunts:
        return None
    hunt_types = {h.hunt_type for h in hunts}
    specialist_hit = _market_matches_tracked_specialist(market, registry.get("tracked_wallets") or [])
    strong = len(hunt_types) >= 2 or HuntType.NEAR_ZERO_ACCUMULATION in hunt_types
    if specialist_hit and strong:
        return OpportunityTier.TIER_A, True
    if specialist_hit:
        return OpportunityTier.TIER_B, True
    if strong:
        return OpportunityTier.TIER_B, False
    return OpportunityTier.TIER_B, False


def _market_matches_tracked_specialist(m: MarketSnapshot, tracked: List[Dict[str, Any]]) -> bool:
    cat = (m.market_category or "default").lower()
    for tw in tracked:
        if tw.get("pattern") != "category_specialist":
            continue
        sp = str(tw.get("specialist_category") or "").lower()
        if sp and sp in cat:
            return True
    return False


def sample_top_wallets_summary(limit: int = TOP_N_DISPLAY) -> str:
    """Human-readable top wallets after a scan (for logs / operator)."""
    data = load_wallets_registry()
    rows = data.get("tracked_wallets") or []
    top = rows[:limit]
    lines = [f"Top {len(top)} tracked wallets by score:"]
    for i, r in enumerate(top, 1):
        lines.append(
            f"  {i}. {r.get('address', '')[:10]}… pattern={r.get('pattern')} score={r.get('score')} cat={r.get('specialist_category')}"
        )
    return "\n".join(lines)


def demo_outputs() -> Dict[str, str]:
    """Sample strings for operator confirmation."""
    return {
        "wallet_scan": sample_top_wallets_summary(3),
        "hunt6_hint": "Hunt 6 fires on cheap YES + long-dated + base rate support (see hunt_near_zero_accumulation).",
        "copy_trade": "Copy tier requires run_hunts_on_market() to return signals first.",
    }
