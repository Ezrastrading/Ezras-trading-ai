"""
Aggressive exit enforcement for Coinbase scalp positions (REST + optional price cache).

State machine: NEW → OPEN → EXIT_PENDING → CLOSED (or FAILED).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_data_dir
from trading_ai.shark.coinbase_scalp_config import CoinbaseScalpConfig, clamp_tp_sl
from trading_ai.shark.coinbase_scalp_notifier import CoinbaseScalpNotifier
from trading_ai.shark.outlets.coinbase import CoinbaseClient

logger = logging.getLogger(__name__)

TradeStatus = str  # NEW | OPEN | EXIT_PENDING | CLOSED | FAILED


def _fmt_base_size(product_id: str, size: float) -> str:
    if product_id.startswith("BTC"):
        return f"{size:.8f}"
    if product_id.startswith("ETH"):
        return f"{size:.6f}"
    return f"{size:.6f}"


def _trade_log_path() -> Any:
    p = shark_data_dir() / "logs" / "coinbase_scalp_trades.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_scalp_trade_log(record: Dict[str, Any]) -> None:
    try:
        with _trade_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        logger.warning("coinbase_scalp trade log write error: %s", exc)


class CoinbaseScalpPositionManager:
    """
    Polls open trades every ``position_check_interval_seconds`` (driven by engine).

    Exit precedence: take-profit (USD) → stop-loss → hard timeout → soft/stagnant timeout.
    """

    def __init__(
        self,
        client: CoinbaseClient,
        config: CoinbaseScalpConfig,
        notifier: Optional[CoinbaseScalpNotifier] = None,
        get_price_cache: Optional[Callable[[], Dict[str, Tuple[float, float, float]]]] = None,
    ) -> None:
        self._client = client
        self._cfg = config
        self._notifier = notifier or CoinbaseScalpNotifier()
        self._get_price_cache = get_price_cache

    def _bid_ask(self, product_id: str) -> Tuple[float, float]:
        now = time.time()
        cache = self._get_price_cache() if self._get_price_cache else {}
        ent = cache.get(product_id)
        if ent is not None:
            bid, ask, ts = ent
            if now - ts <= self._cfg.price_cache_max_age_seconds and (bid > 0 or ask > 0):
                return bid, ask
        return self._client.get_product_price(product_id)

    def check_positions(
        self,
        state: Dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> None:
        tnow = now if now is not None else time.time()
        positions: List[Dict[str, Any]] = list(state.get("positions") or [])
        if not positions:
            return

        out: List[Dict[str, Any]] = []
        for pos in positions:
            st = str(pos.get("status") or "OPEN")
            if st == "CLOSED":
                continue
            if st == "EXIT_PENDING":
                self._handle_exit_pending(state, pos, tnow, out)
                continue
            if st in ("NEW", "OPEN"):
                self._handle_open(state, pos, tnow, out)
                continue
            if st == "FAILED":
                out.append(pos)
                continue
            out.append(pos)

        state["positions"] = out

    def _handle_exit_pending(
        self,
        state: Dict[str, Any],
        pos: Dict[str, Any],
        now: float,
        out: List[Dict[str, Any]],
    ) -> None:
        oid = str(pos.get("exit_order_id") or "")
        submitted = float(pos.get("exit_submitted_at") or 0.0)
        if not oid:
            pos["status"] = "OPEN"
            pos.pop("exit_submitted_at", None)
            out.append(pos)
            return

        terminal, realized = self._order_terminal_filled(oid)
        if terminal:
            self._finalize_closed(state, pos, now, realized, out)
            return

        if now - submitted > self._cfg.exit_poll_max_seconds:
            logger.error(
                "scalp EXIT_PENDING stuck trade_id=%s order=%s — marking FAILED",
                pos.get("trade_id"),
                oid,
            )
            pos["status"] = "FAILED"
            pos["exit_reason"] = pos.get("exit_reason") or "exit_stuck"
            append_scalp_trade_log(
                {
                    "event": "failed",
                    "trade_id": pos.get("trade_id"),
                    "product_id": pos.get("product_id"),
                    "exit_order_id": oid,
                    "ts": now,
                }
            )
            out.append(pos)
            return

        out.append(pos)

    def _order_terminal_filled(self, order_id: str) -> Tuple[bool, float]:
        fills = self._client.get_fills(order_id)
        realized = 0.0
        for f in fills:
            try:
                px = float(f.get("price") or f.get("average_filled_price") or 0.0)
                sz = float(f.get("size") or f.get("filled_size") or 0.0)
                side = str(f.get("side") or "").upper()
                if px > 0 and sz > 0:
                    realized += px * sz * (1.0 if side == "SELL" else -1.0)
            except (TypeError, ValueError):
                continue
        if fills:
            return True, realized

        try:
            raw = self._client.get_order(order_id)
        except Exception as exc:
            logger.warning("scalp get_order %s: %s", order_id, exc)
            return False, 0.0
        status = str(raw.get("status") or raw.get("order_status") or "").upper()
        if status in ("FILLED", "CANCELLED", "EXPIRED", "FAILED", "DONE"):
            return True, realized
        return False, 0.0

    def _finalize_closed(
        self,
        state: Dict[str, Any],
        pos: Dict[str, Any],
        now: float,
        _realized_hint: float,
        out: List[Dict[str, Any]],
    ) -> None:
        pid = str(pos.get("product_id") or "")
        entry = float(pos.get("entry_price") or 0.0)
        cost = float(pos.get("cost_usd") or 0.0)
        size_base = float(pos.get("size_base") or 0.0)
        bid, _ = self._bid_ask(pid)
        exit_px = bid if bid > 0 else entry
        pnl = exit_px * size_base - cost if size_base > 0 and cost > 0 else 0.0

        pos["status"] = "CLOSED"
        pos["exit_time"] = now
        pos["realized_pnl_usd"] = round(pnl, 6)
        forced = bool(pos.get("forced_timeout"))

        append_scalp_trade_log(
            {
                "event": "closed",
                "trade_id": pos.get("trade_id"),
                "product_id": pid,
                "entry_ts": pos.get("entry_time"),
                "exit_ts": now,
                "entry_price": entry,
                "exit_price": exit_px,
                "size_base": size_base,
                "cost_usd": cost,
                "realized_pnl_usd": pnl,
                "exit_reason": pos.get("exit_reason"),
                "forced_timeout": forced,
            }
        )

        state["daily_pnl_usd"] = float(state.get("daily_pnl_usd") or 0.0) + pnl
        if pnl < 0:
            state["consecutive_losses"] = int(state.get("consecutive_losses") or 0) + 1
        else:
            state["consecutive_losses"] = 0

        summary = (
            f"🔷 Scalp CLOSED {pid}\n"
            f"P&L ${pnl:+.4f} ({pos.get('exit_reason')})\n"
            f"timeout_forced={'yes' if forced else 'no'}"
        )
        if not pos.get("exit_notified"):
            if self._notifier.notify_exit(pos, summary):
                pos["exit_notified"] = True

        # Drop from active list — audit is in JSONL
        _ = out  # closed not appended

    def _handle_open(
        self,
        state: Dict[str, Any],
        pos: Dict[str, Any],
        now: float,
        out: List[Dict[str, Any]],
    ) -> None:
        trade_id = str(pos.get("trade_id") or "")
        pid = str(pos.get("product_id") or "")
        entry_t = float(pos.get("entry_time") or 0.0)
        entry = float(pos.get("entry_price") or 0.0)
        size_base = float(pos.get("size_base") or 0.0)
        cost = float(pos.get("cost_usd") or 0.0)
        tp = float(pos.get("take_profit_usd") or clamp_tp_sl(self._cfg)[0])
        sl = float(pos.get("stop_loss_usd") or clamp_tp_sl(self._cfg)[1])

        bid, ask = self._bid_ask(pid)
        mark = bid if bid > 0 else (ask if ask > 0 else entry)
        elapsed = now - entry_t if entry_t > 0 else 0.0

        pnl_usd = mark * size_base - cost if size_base > 0 and cost > 0 else 0.0
        pnl_pct = (pnl_usd / cost) if cost > 1e-9 else 0.0

        pos["last_mark_price"] = mark
        pos["last_unrealized_pnl_usd"] = round(pnl_usd, 6)
        pos["last_unrealized_pnl_pct"] = round(pnl_pct * 100.0, 6)
        pos["last_elapsed_seconds"] = int(elapsed)

        logger.info(
            "SCALP CHECK trade_id=%s product=%s pnl=$%.4f (%.4f%%) elapsed=%ds status=%s",
            trade_id,
            pid,
            pnl_usd,
            pnl_pct * 100.0,
            int(elapsed),
            pos.get("status"),
        )

        append_scalp_trade_log(
            {
                "event": "tick",
                "trade_id": trade_id,
                "product_id": pid,
                "entry_ts": entry_t,
                "entry_price": entry,
                "size_base": size_base,
                "take_profit_usd": tp,
                "stop_loss_usd": sl,
                "mark": mark,
                "unrealized_pnl_usd": pnl_usd,
                "elapsed_seconds": int(elapsed),
                "ts": now,
            }
        )

        reason = ""
        forced_timeout = False
        if pnl_usd >= tp:
            reason = "take_profit"
        elif pnl_usd <= sl:
            reason = "stop_loss"
        elif (
            elapsed >= self._cfg.soft_timeout_seconds
            and abs(pnl_usd) <= self._cfg.stagnant_pnl_usd_abs
        ):
            reason = "soft_timeout_stagnant"
        elif elapsed >= self._cfg.hard_timeout_seconds:
            reason = "hard_timeout"
            forced_timeout = True
        if not reason:
            out.append(pos)
            return

        self._submit_exit(state, pos, now, reason, forced_timeout, out)

    def _submit_exit(
        self,
        state: Dict[str, Any],
        pos: Dict[str, Any],
        now: float,
        reason: str,
        forced_timeout: bool,
        out: List[Dict[str, Any]],
    ) -> None:
        if pos.get("exit_submitted_at"):
            out.append(pos)
            return

        pid = str(pos.get("product_id") or "")
        size_base = float(pos.get("size_base") or 0.0)
        base_str = _fmt_base_size(pid, size_base)

        pos["exit_reason"] = reason
        pos["forced_timeout"] = forced_timeout
        pos["status"] = "EXIT_PENDING"
        pos["exit_submitted_at"] = now

        r = self._client.place_market_sell(pid, base_str)
        if not r.success:
            logger.warning(
                "scalp exit sell failed trade_id=%s %s: %s — retry next tick",
                pos.get("trade_id"),
                pid,
                r.reason,
            )
            pos["status"] = "OPEN"
            pos.pop("exit_submitted_at", None)
            out.append(pos)
            return

        pos["exit_order_id"] = r.order_id
        append_scalp_trade_log(
            {
                "event": "exit_submit",
                "trade_id": pos.get("trade_id"),
                "product_id": pid,
                "exit_order_id": r.order_id,
                "reason": reason,
                "forced_timeout": forced_timeout,
                "ts": now,
            }
        )
        out.append(pos)


def new_trade_from_buy(
    *,
    product_id: str,
    entry_price: float,
    cost_usd: float,
    order_id: str,
    cfg: CoinbaseScalpConfig,
) -> Dict[str, Any]:
    now = time.time()
    tp, sl = clamp_tp_sl(cfg)
    tid = str(uuid.uuid4())
    size_base = cost_usd / entry_price if entry_price > 0 else 0.0
    return {
        "trade_id": tid,
        "status": "OPEN",
        "product_id": product_id,
        "entry_time": now,
        "entry_price": entry_price,
        "size_base": size_base,
        "cost_usd": cost_usd,
        "buy_order_id": order_id,
        "take_profit_usd": tp,
        "stop_loss_usd": sl,
        "peak_price": entry_price,
        "exit_submitted_at": None,
        "exit_reason": None,
        "exit_notified": False,
        "exit_order_id": None,
        "forced_timeout": False,
    }


def reset_daily_if_needed(state: Dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl_usd"] = 0.0
        state["daily_pnl_date"] = today
