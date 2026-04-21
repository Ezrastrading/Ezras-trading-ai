"""
Position lifecycle: PnL vs clip-level target/stop, soft/hard timeouts, emergency liquidity exit.

State machine: NEW → OPEN → EXIT_PENDING → CLOSED (or FAILED). At most one exit submission per trade.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

from trading_ai.shark.kalshi_scalp_config import KalshiScalpConfig
from trading_ai.shark.kalshi_scalp_market_filter import (
    LiquiditySnapshot,
    evaluate_scalp_filter,
    mark_price_cents_for_pnl,
    normalize_fill_price_to_probability,
    orderbook_depth_at_best,
    parse_orderbook_yes_no_best_bid_ask_cents,
    unrealized_pnl_usd,
)
from trading_ai.shark.kalshi_scalp_notifier import KalshiScalpNotifier
from trading_ai.shark.outlets.kalshi import KalshiClient
from trading_ai.shark.models import OrderResult

if TYPE_CHECKING:
    from trading_ai.shark.kalshi_scalp_scanner import ScalpSetup

logger = logging.getLogger(__name__)


@dataclass
class KalshiScalpMetrics:
    """Engine-level counters (caps are aspirations — see config)."""

    scans_performed: int = 0
    candidates_found: int = 0
    entries_approved: int = 0
    entries_skipped: int = 0
    exits_by_target: int = 0
    exits_by_stop: int = 0
    exits_by_timeout_soft: int = 0
    exits_by_timeout_hard: int = 0
    exits_by_emergency: int = 0
    duplicate_exit_prevention_hits: int = 0


@dataclass
class ScalpTrade:
    trade_id: str
    state: str  # NEW OPEN EXIT_PENDING CLOSED FAILED
    market_ticker: str
    family: str
    side: str
    entry_price_prob: float
    size_contracts: float
    profit_target_usd: float
    stop_loss_usd: float
    soft_timeout_sec: float
    hard_timeout_sec: float
    entry_time: float
    exit_submitted_at: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_notified: bool = False
    entry_bid_depth: float = 0.0
    last_elapsed_sec: float = 0.0
    last_pnl_usd: float = 0.0
    realized_pnl_usd: Optional[float] = None
    raw_entry: Dict[str, Any] = field(default_factory=dict)
    raw_exit: Dict[str, Any] = field(default_factory=dict)


def new_trade_id() -> str:
    return str(uuid.uuid4())


class KalshiScalpPositionManager:
    """Evaluates open clips every ``position_check_interval``; submits a single exit per trade."""

    def __init__(
        self,
        cfg: KalshiScalpConfig,
        *,
        client: Optional[KalshiClient] = None,
        notifier: Optional[KalshiScalpNotifier] = None,
        metrics: Optional[KalshiScalpMetrics] = None,
    ) -> None:
        import os

        base = cfg.kalshi_api_base or os.environ.get(
            "KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"
        ).rstrip("/")
        self.cfg = cfg
        self.client = client or KalshiClient(base_url=base)
        self.notifier = notifier or KalshiScalpNotifier()
        self.metrics = metrics

    def create_trade_from_setup(self, setup: "ScalpSetup") -> ScalpTrade:
        tid = new_trade_id()
        return ScalpTrade(
            trade_id=tid,
            state="NEW",
            market_ticker=setup.market_ticker,
            family=setup.family.value,
            side=setup.side,
            entry_price_prob=max(0.01, min(0.99, setup.ask_cents / 100.0)),
            size_contracts=float(setup.contracts),
            profit_target_usd=float(self.cfg.default_profit_target_dollars),
            stop_loss_usd=float(self.cfg.default_stop_loss_dollars),
            soft_timeout_sec=float(self.cfg.soft_timeout_seconds),
            hard_timeout_sec=float(self.cfg.hard_timeout_seconds),
            entry_time=time.time(),
            entry_bid_depth=setup.liquidity.exit_size_for_side(setup.side),
        )

    def _orderbook(self, ticker: str) -> Dict[str, Any]:
        import urllib.parse

        return self.client._request("GET", f"/markets/{urllib.parse.quote(ticker.strip(), safe='')}/orderbook")

    def execute_entry(self, trade: ScalpTrade, setup: "ScalpSetup") -> ScalpTrade:
        """Submit opening buy; paper mode simulates immediate fill at ask."""
        if self.cfg.paper_mode or not self.cfg.execution_enabled:
            trade.state = "OPEN"
            trade.entry_price_prob = max(0.01, min(0.99, setup.ask_cents / 100.0))
            trade.raw_entry = {"paper": True, "ask_cents": setup.ask_cents}
            self.notifier.on_entry_filled(
                {
                    "trade_id": trade.trade_id,
                    "market_ticker": trade.market_ticker,
                    "family": trade.family,
                    "side": trade.side,
                    "entry_price": trade.entry_price_prob,
                    "size": trade.size_contracts,
                    "target_usd": trade.profit_target_usd,
                    "stop_usd": trade.stop_loss_usd,
                    "soft_timeout_sec": trade.soft_timeout_sec,
                    "hard_timeout_sec": trade.hard_timeout_sec,
                }
            )
            return trade

        res: OrderResult = self.client.place_order(
            ticker=trade.market_ticker,
            side=trade.side,
            count=int(trade.size_contracts),
            action="buy",
            order_type="limit",
            limit_price_cents=setup.ask_cents,
            fill_timeout_sec=max(3.0, float(self.cfg.position_check_interval_seconds) * 2),
            skip_pretrade_buy_gates=True,
        )
        trade.raw_entry = dict(res.raw or {})
        if getattr(res, "success", True) and res.filled_size and res.filled_size > 0:
            trade.state = "OPEN"
            trade.entry_price_prob = normalize_fill_price_to_probability(
                res.filled_price if res.filled_price else setup.ask_cents / 100.0
            )
            trade.size_contracts = float(res.filled_size)
        else:
            trade.state = "FAILED"
        self.notifier.on_entry_filled(
            {
                "trade_id": trade.trade_id,
                "market_ticker": trade.market_ticker,
                "family": trade.family,
                "side": trade.side,
                "entry_price": trade.entry_price_prob,
                "size": trade.size_contracts,
                "target_usd": trade.profit_target_usd,
                "stop_usd": trade.stop_loss_usd,
                "soft_timeout_sec": trade.soft_timeout_sec,
                "hard_timeout_sec": trade.hard_timeout_sec,
                "success": trade.state == "OPEN",
                "reason": getattr(res, "reason", None),
            }
        )
        return trade

    def execute_exit(self, trade: ScalpTrade, reason: str) -> ScalpTrade:
        """Submit closing sell once; paper fills at bid."""
        if trade.exit_submitted_at is not None:
            if self.metrics:
                self.metrics.duplicate_exit_prevention_hits += 1
            self.notifier.on_duplicate_exit_prevented({"trade_id": trade.trade_id, "reason": reason})
            return trade

        trade.exit_submitted_at = time.time()
        trade.exit_reason = reason
        trade.state = "EXIT_PENDING"
        self.notifier.on_exit_submitted(
            {"trade_id": trade.trade_id, "market_ticker": trade.market_ticker, "exit_reason": reason}
        )

        if self.cfg.paper_mode or not self.cfg.execution_enabled:
            ob = self._orderbook(trade.market_ticker)
            yb, ya, nb, na = parse_orderbook_yes_no_best_bid_ask_cents(ob)
            ybs, yas, nbs, nas = orderbook_depth_at_best(ob)
            liq = LiquiditySnapshot(yb, ya, nb, na, ybs, yas, nbs, nas)
            bid_c = mark_price_cents_for_pnl(trade.side, liq)
            if bid_c is None:
                bid_c = int(round(trade.entry_price_prob * 100))
            exit_prob = max(0.01, min(0.99, bid_c / 100.0))
            trade.realized_pnl_usd = unrealized_pnl_usd(
                trade.side, trade.entry_price_prob, exit_prob, trade.size_contracts
            )
            trade.state = "CLOSED"
            trade.raw_exit = {"paper": True, "bid_cents": bid_c}
            self._notify_exit_filled(trade)
            return trade

        res: OrderResult = self.client.place_order(
            ticker=trade.market_ticker,
            side=trade.side,
            count=max(1, int(round(trade.size_contracts))),
            action="sell",
            order_type="market",
            fill_timeout_sec=max(3.0, float(self.cfg.position_check_interval_seconds) * 2),
        )
        trade.raw_exit = dict(res.raw or {})
        if getattr(res, "success", True) and res.filled_size and res.filled_size > 0:
            exit_prob = normalize_fill_price_to_probability(res.filled_price)
            trade.realized_pnl_usd = unrealized_pnl_usd(
                trade.side, trade.entry_price_prob, exit_prob, float(res.filled_size)
            )
            trade.state = "CLOSED"
        else:
            trade.state = "FAILED"
        self._notify_exit_filled(trade)
        return trade

    def _notify_exit_filled(self, trade: ScalpTrade) -> None:
        if trade.exit_notified:
            return
        trade.exit_notified = True
        self.notifier.on_exit_filled(
            {
                "trade_id": trade.trade_id,
                "market_ticker": trade.market_ticker,
                "family": trade.family,
                "exit_reason": trade.exit_reason,
                "realized_pnl_usd": trade.realized_pnl_usd,
                "state": trade.state,
            }
        )

    def evaluate(self, trade: ScalpTrade, market_row: Dict[str, Any]) -> ScalpTrade:
        """
        One monitoring tick: refresh book, compute PnL and elapsed, maybe exit.

        ``market_row`` is the latest GET /markets/{ticker} payload inner dict if available; used for filter re-check.
        """
        if trade.state not in ("OPEN", "EXIT_PENDING"):
            return trade

        now = time.time()
        elapsed = now - trade.entry_time
        trade.last_elapsed_sec = elapsed

        try:
            ob = self._orderbook(trade.market_ticker)
        except Exception as exc:
            logger.warning("position check orderbook failed %s: %s", trade.market_ticker, exc)
            return trade

        fr = evaluate_scalp_filter(market_row, ob, cfg=self.cfg, now=now)
        if fr.liquidity is not None:
            liq = fr.liquidity
        else:
            yb, ya, nb, na = parse_orderbook_yes_no_best_bid_ask_cents(ob)
            ybs, yas, nbs, nas = orderbook_depth_at_best(ob)
            liq = LiquiditySnapshot(yb, ya, nb, na, ybs, yas, nbs, nas)

        bid_c = mark_price_cents_for_pnl(trade.side, liq)
        if bid_c is None:
            return trade
        mark = bid_c / 100.0
        pnl = unrealized_pnl_usd(trade.side, trade.entry_price_prob, mark, trade.size_contracts)
        trade.last_pnl_usd = pnl

        self.notifier.on_position_check(
            {
                "trade_id": trade.trade_id,
                "market_ticker": trade.market_ticker,
                "family": trade.family,
                "side": trade.side,
                "elapsed_sec": elapsed,
                "pnl_usd": pnl,
                "mark_prob": mark,
            }
        )

        if pnl >= trade.profit_target_usd:
            if self.metrics:
                self.metrics.exits_by_target += 1
            return self.execute_exit(trade, "take_profit")

        if pnl <= trade.stop_loss_usd:
            if self.metrics:
                self.metrics.exits_by_stop += 1
            return self.execute_exit(trade, "stop_loss")

        if elapsed >= trade.hard_timeout_sec:
            if self.metrics:
                self.metrics.exits_by_timeout_hard += 1
            return self.execute_exit(trade, "hard_timeout")

        cur_depth = liq.exit_size_for_side(trade.side)
        if trade.entry_bid_depth > 0 and cur_depth < trade.entry_bid_depth * self.cfg.emergency_depth_ratio:
            if self.metrics:
                self.metrics.exits_by_emergency += 1
            return self.execute_exit(trade, "emergency_liquidity")

        if (
            elapsed >= trade.soft_timeout_sec
            and abs(pnl) < self.cfg.stagnant_pnl_abs_usd
            and pnl < trade.profit_target_usd * 0.5
        ):
            if self.metrics:
                self.metrics.exits_by_timeout_soft += 1
            return self.execute_exit(trade, "soft_timeout_stagnant")

        return trade
