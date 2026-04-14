"""Master strategy registry — 12 proven strategies across 4 categories; gates + CEO/journal wiring."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.shark.models import HuntType

logger = logging.getLogger(__name__)

_STATE_FILE = "master_strategy_state.json"


class StrategyType(Enum):
    PREDICTION_MARKET = "prediction"
    CRYPTO = "crypto"
    STOCKS = "stocks"
    OPTIONS = "options"


class StrategyID(Enum):
    PM1_NEAR_RESOLUTION = "pm1"
    PM2_CROSS_PLATFORM_DIVERGENCE = "pm2"
    PM3_EVENT_MOMENTUM = "pm3"
    C1_GRID_TRADING = "c1"
    C2_MOMENTUM_SCALP = "c2"
    C3_KALSHI_COINBASE_ARB = "c3"
    S1_EVENT_DRIVEN_ETF = "s1"
    S2_OPENING_RANGE_BREAKOUT = "s2"
    S3_NEWS_MOMENTUM = "s3"
    O1_ZERO_DTE_CREDIT_SPREAD = "o1"
    O2_IV_CRUSH = "o2"
    O3_KALSHI_OPTIONS_ARB = "o3"


@dataclass(frozen=True)
class StrategyConfig:
    id: StrategyID
    type: StrategyType
    name: str
    description: str
    min_capital: float
    expected_win_rate: float
    expected_edge_per_trade: float
    max_trades_per_day: int
    compatible_avenues: List[str]
    enabled: bool = False
    activation_capital: float = 0.0


_STRATEGY_REGISTRY: Dict[StrategyID, StrategyConfig] = {
    StrategyID.PM1_NEAR_RESOLUTION: StrategyConfig(
        id=StrategyID.PM1_NEAR_RESOLUTION,
        type=StrategyType.PREDICTION_MARKET,
        name="Near Resolution Scalp",
        description=(
            "Buy heavily favored side of markets resolving within hours. "
            "Win rate 85–95% on liquid events."
        ),
        min_capital=10.0,
        expected_win_rate=0.90,
        expected_edge_per_trade=0.05,
        max_trades_per_day=50,
        compatible_avenues=["kalshi"],
        enabled=True,
        activation_capital=10.0,
    ),
    StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE: StrategyConfig(
        id=StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE,
        type=StrategyType.PREDICTION_MARKET,
        name="Cross-Platform Divergence",
        description=(
            "Trade price gaps between Kalshi and other venues (e.g. Polymarket, Metaculus) "
            "on the same events."
        ),
        min_capital=25.0,
        expected_win_rate=0.70,
        expected_edge_per_trade=0.10,
        max_trades_per_day=15,
        compatible_avenues=["kalshi", "polymarket", "manifold"],
        enabled=True,
        activation_capital=25.0,
    ),
    StrategyID.PM3_EVENT_MOMENTUM: StrategyConfig(
        id=StrategyID.PM3_EVENT_MOMENTUM,
        type=StrategyType.PREDICTION_MARKET,
        name="Event Momentum",
        description=(
            "Follow large short-horizon moves in prediction markets; Kalshi momentum and "
            "statistical windows."
        ),
        min_capital=25.0,
        expected_win_rate=0.65,
        expected_edge_per_trade=0.12,
        max_trades_per_day=10,
        compatible_avenues=["kalshi"],
        enabled=True,
        activation_capital=25.0,
    ),
    StrategyID.C1_GRID_TRADING: StrategyConfig(
        id=StrategyID.C1_GRID_TRADING,
        type=StrategyType.CRYPTO,
        name="BTC Grid Trading",
        description="Buy/sell BTC at fixed intervals; profit from oscillation.",
        min_capital=100.0,
        expected_win_rate=0.75,
        expected_edge_per_trade=0.008,
        max_trades_per_day=20,
        compatible_avenues=["coinbase"],
        enabled=False,
        activation_capital=100.0,
    ),
    StrategyID.C2_MOMENTUM_SCALP: StrategyConfig(
        id=StrategyID.C2_MOMENTUM_SCALP,
        type=StrategyType.CRYPTO,
        name="Crypto Momentum Scalp",
        description="Enter on 1.5%+ moves in 15min; target quick mean-reversion or follow-through.",
        min_capital=50.0,
        expected_win_rate=0.62,
        expected_edge_per_trade=0.006,
        max_trades_per_day=15,
        compatible_avenues=["coinbase", "polymarket", "kalshi"],
        enabled=False,
        activation_capital=50.0,
    ),
    StrategyID.C3_KALSHI_COINBASE_ARB: StrategyConfig(
        id=StrategyID.C3_KALSHI_COINBASE_ARB,
        type=StrategyType.CRYPTO,
        name="Kalshi-Coinbase Arbitrage",
        description="Trade gap between Kalshi BTC implied price and spot (Coinbase/Binance reference).",
        min_capital=50.0,
        expected_win_rate=0.80,
        expected_edge_per_trade=0.02,
        max_trades_per_day=8,
        compatible_avenues=["kalshi", "coinbase"],
        enabled=False,
        activation_capital=50.0,
    ),
    StrategyID.S1_EVENT_DRIVEN_ETF: StrategyConfig(
        id=StrategyID.S1_EVENT_DRIVEN_ETF,
        type=StrategyType.STOCKS,
        name="Event-Driven ETF",
        description="Use Kalshi event probabilities to tilt TLT/QQQ/SPY around macro events.",
        min_capital=200.0,
        expected_win_rate=0.70,
        expected_edge_per_trade=0.05,
        max_trades_per_day=2,
        compatible_avenues=["robinhood", "webull"],
        enabled=False,
        activation_capital=200.0,
    ),
    StrategyID.S2_OPENING_RANGE_BREAKOUT: StrategyConfig(
        id=StrategyID.S2_OPENING_RANGE_BREAKOUT,
        type=StrategyType.STOCKS,
        name="Opening Range Breakout",
        description="SPY/QQQ breakout from first 15 minutes range.",
        min_capital=200.0,
        expected_win_rate=0.60,
        expected_edge_per_trade=0.01,
        max_trades_per_day=3,
        compatible_avenues=["robinhood", "webull"],
        enabled=False,
        activation_capital=200.0,
    ),
    StrategyID.S3_NEWS_MOMENTUM: StrategyConfig(
        id=StrategyID.S3_NEWS_MOMENTUM,
        type=StrategyType.STOCKS,
        name="News Momentum",
        description="Pre-position before major releases using prediction-market probability signals.",
        min_capital=200.0,
        expected_win_rate=0.65,
        expected_edge_per_trade=0.04,
        max_trades_per_day=1,
        compatible_avenues=["robinhood", "webull"],
        enabled=False,
        activation_capital=200.0,
    ),
    StrategyID.O1_ZERO_DTE_CREDIT_SPREAD: StrategyConfig(
        id=StrategyID.O1_ZERO_DTE_CREDIT_SPREAD,
        type=StrategyType.OPTIONS,
        name="0DTE Credit Spread",
        description="Short-dated credit spreads on index ETFs; defined risk, theta harvest.",
        min_capital=500.0,
        expected_win_rate=0.75,
        expected_edge_per_trade=0.03,
        max_trades_per_day=2,
        compatible_avenues=["tastytrade"],
        enabled=False,
        activation_capital=500.0,
    ),
    StrategyID.O2_IV_CRUSH: StrategyConfig(
        id=StrategyID.O2_IV_CRUSH,
        type=StrategyType.OPTIONS,
        name="IV Crush Play",
        description="Position around events for implied-volatility mean reversion.",
        min_capital=500.0,
        expected_win_rate=0.68,
        expected_edge_per_trade=0.15,
        max_trades_per_day=1,
        compatible_avenues=["tastytrade"],
        enabled=False,
        activation_capital=500.0,
    ),
    StrategyID.O3_KALSHI_OPTIONS_ARB: StrategyConfig(
        id=StrategyID.O3_KALSHI_OPTIONS_ARB,
        type=StrategyType.OPTIONS,
        name="Kalshi-Options Arbitrage",
        description="Cross-market: macro probabilities vs listed options on bonds/indices.",
        min_capital=500.0,
        expected_win_rate=0.72,
        expected_edge_per_trade=0.08,
        max_trades_per_day=2,
        compatible_avenues=["kalshi", "tastytrade"],
        enabled=False,
        activation_capital=500.0,
    ),
}

# HuntType → StrategyID for gates. None = not gated by master strategies (always eligible).
HUNT_TO_STRATEGY: Dict[HuntType, Optional[StrategyID]] = {
    HuntType.NEAR_RESOLUTION: StrategyID.PM1_NEAR_RESOLUTION,
    HuntType.KALSHI_NEAR_CLOSE: StrategyID.PM1_NEAR_RESOLUTION,
    HuntType.DEAD_MARKET_CONVERGENCE: StrategyID.PM1_NEAR_RESOLUTION,
    HuntType.STRUCTURAL_ARBITRAGE: StrategyID.PM1_NEAR_RESOLUTION,
    HuntType.CROSS_PLATFORM_MISPRICING: StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE,
    HuntType.KALSHI_CONVERGENCE: StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE,
    HuntType.KALSHI_METACULUS_AGREE: StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE,
    HuntType.KALSHI_METACULUS_DIVERGE: StrategyID.PM2_CROSS_PLATFORM_DIVERGENCE,
    HuntType.KALSHI_MOMENTUM: StrategyID.PM3_EVENT_MOMENTUM,
    HuntType.STATISTICAL_WINDOW: StrategyID.PM3_EVENT_MOMENTUM,
    HuntType.VOLUME_SPIKE: StrategyID.PM3_EVENT_MOMENTUM,
    HuntType.LIQUIDITY_IMBALANCE_FADE: StrategyID.PM3_EVENT_MOMENTUM,
    HuntType.CRYPTO_SCALP: StrategyID.C2_MOMENTUM_SCALP,
    HuntType.PURE_ARBITRAGE: StrategyID.C3_KALSHI_COINBASE_ARB,
    HuntType.ORDER_BOOK_IMBALANCE: StrategyID.C2_MOMENTUM_SCALP,
    HuntType.OPTIONS_BINARY: StrategyID.O1_ZERO_DTE_CREDIT_SPREAD,
    HuntType.NEAR_ZERO_ACCUMULATION: StrategyID.PM1_NEAR_RESOLUTION,
}


def _state_path() -> Path:
    return shark_state_path(_STATE_FILE)


def _default_enabled_map() -> Dict[str, bool]:
    return {sid.value: cfg.enabled for sid, cfg in _STRATEGY_REGISTRY.items()}


def _load_enabled_overrides() -> Dict[str, bool]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("enabled"), dict):
            return {str(k): bool(v) for k, v in raw["enabled"].items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save_enabled_overrides(overrides: Dict[str, bool]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"enabled": overrides}, indent=2), encoding="utf-8")
    tmp.replace(p)


def _effective_enabled_map() -> Dict[str, bool]:
    base = _default_enabled_map()
    base.update(_load_enabled_overrides())
    return base


def is_strategy_enabled(sid: StrategyID) -> bool:
    return _effective_enabled_map().get(sid.value, _STRATEGY_REGISTRY[sid].enabled)


def strategy_allowed_for_hunt(hunt_type: HuntType) -> bool:
    sid = HUNT_TO_STRATEGY.get(hunt_type)
    if sid is None:
        return True
    return is_strategy_enabled(sid)


def get_strategy_config(sid: StrategyID) -> StrategyConfig:
    return _STRATEGY_REGISTRY[sid]


def get_registry_snapshot() -> Dict[StrategyID, StrategyConfig]:
    """Configs with default flags; use ``is_strategy_enabled`` for live gates."""
    return dict(_STRATEGY_REGISTRY)


STRATEGY_REGISTRY = _STRATEGY_REGISTRY


def get_active_strategies(capital: float, active_avenues: List[str]) -> List[StrategyConfig]:
    avenues_l = {a.lower() for a in active_avenues}
    out: List[StrategyConfig] = []
    for sid, cfg in _STRATEGY_REGISTRY.items():
        if not is_strategy_enabled(sid):
            continue
        if capital < cfg.min_capital:
            continue
        if not any(a.lower() in avenues_l for a in cfg.compatible_avenues):
            continue
        out.append(cfg)
    return out


def auto_activate_strategies(capital: float, active_avenues: List[str]) -> List[StrategyID]:
    """Enable strategies whose activation capital and avenue compatibility are satisfied."""
    avenues_l = {a.lower() for a in active_avenues}
    em = _effective_enabled_map()
    activated: List[StrategyID] = []
    changed = False
    for sid, cfg in _STRATEGY_REGISTRY.items():
        if em.get(sid.value, cfg.enabled):
            continue
        if capital < cfg.activation_capital:
            continue
        if not any(a.lower() in avenues_l for a in cfg.compatible_avenues):
            continue
        em[sid.value] = True
        activated.append(sid)
        changed = True
    if changed:
        _save_enabled_overrides(em)
        logger.info("Auto-activated strategies: %s", [x.value for x in activated])
    return activated


def set_strategy_enabled(sid: StrategyID, enabled: bool) -> None:
    em = _effective_enabled_map()
    em[sid.value] = enabled
    _save_enabled_overrides(em)


def apply_strategy_enabled_changes(changes: Dict[str, Any]) -> None:
    """Apply CEO ``strategy_enabled`` map: keys are strategy ids (e.g. pm1, c1)."""
    if not changes:
        return
    em = _effective_enabled_map()
    valid = {x.value for x in StrategyID}
    for k, v in changes.items():
        key = str(k).strip().lower()
        if key not in valid:
            continue
        try:
            em[key] = bool(v)
        except (TypeError, ValueError):
            continue
    _save_enabled_overrides(em)


def _stats_for_strategy(sid: StrategyID, by_hunt: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    hunt_keys: Set[str] = {ht.value for ht, s in HUNT_TO_STRATEGY.items() if s == sid}
    n_total = 0
    wins_total = 0
    pnl_total = 0.0
    for hk in hunt_keys:
        b = by_hunt.get(hk)
        if not isinstance(b, dict):
            continue
        n_total += int(b.get("n", 0) or 0)
        wins_total += int(b.get("wins", 0) or 0)
        pnl_total += float(b.get("pnl", 0) or 0)
    wr: Optional[float] = None
    if n_total > 0:
        wr = round(wins_total / n_total, 6)
    return {
        "trades": n_total,
        "wins": wins_total,
        "win_rate": wr,
        "total_pnl": round(pnl_total, 4),
    }


def get_strategy_performance_summary() -> Dict[str, Any]:
    from trading_ai.shark.trade_journal import get_summary_stats

    stats = get_summary_stats()
    by_hunt = stats.get("by_hunt_type") or {}
    if not isinstance(by_hunt, dict):
        by_hunt = {}

    out: Dict[str, Any] = {}
    for sid, cfg in _STRATEGY_REGISTRY.items():
        perf = _stats_for_strategy(sid, by_hunt)
        out[sid.value] = {
            "name": cfg.name,
            "type": cfg.type.value,
            "enabled": is_strategy_enabled(sid),
            "min_capital": cfg.min_capital,
            "expected_win_rate": cfg.expected_win_rate,
            "actual_win_rate": perf["win_rate"],
            "actual_pnl": perf["total_pnl"],
            "journal_trades": perf["trades"],
        }
    return out


def filter_hunt_signals_by_strategy(signals: List[Any], *, log_counts: bool = True) -> List[Any]:
    """Drop HuntSignals whose hunt type maps to a disabled strategy."""
    from trading_ai.shark.models import HuntSignal

    kept: List[Any] = []
    counts: Dict[str, int] = {}
    for sig in signals:
        if not isinstance(sig, HuntSignal):
            kept.append(sig)
            continue
        sid = HUNT_TO_STRATEGY.get(sig.hunt_type)
        if sid is not None and not is_strategy_enabled(sid):
            logger.debug(
                "Master strategy %s disabled — skipping hunt %s",
                sid.value,
                sig.hunt_type.value,
            )
            continue
        kept.append(sig)
        if sid is not None:
            counts[sid.value] = counts.get(sid.value, 0) + 1
    if log_counts and counts:
        logger.info("Master strategy hunt signals retained: %s", counts)
    return kept
