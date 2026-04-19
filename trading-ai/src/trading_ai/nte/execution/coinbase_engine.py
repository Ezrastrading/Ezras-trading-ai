"""Coinbase Avenue 1 — limit-first execution, WS+REST data, fixed risk, NTE hooks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.nte.config.coinbase_avenue1_launch import (
    load_coinbase_avenue1_launch,
    spread_cap_pct,
    vol_cap_bps,
)
from trading_ai.nte.config.settings import NTECoinbaseSettings, load_nte_settings
from trading_ai.nte.data.feature_engine import compute_features
from trading_ai.nte.data.market_classifier import classify_market
from trading_ai.nte.data.market_state import ProductMarketState
from trading_ai.nte.data.ws_advanced_trade import AdvancedTradeWSFeed
from trading_ai.nte.ceo.iteration_engine import IterationEngine
from trading_ai.nte.execution import risk as risk_mod
from trading_ai.nte.execution.state import (
    load_state,
    new_position_id,
    open_positions_list,
    save_state,
)
from trading_ai.nte.goals.evaluation import GoalEvaluator
from trading_ai.nte.learning.global_learning_engine import GlobalLearningEngine
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.rewards.engine import RewardEngine
from trading_ai.nte.monitoring.execution_counters import bump as bump_exec_counter
from trading_ai.nte.paths import nte_system_health_path
from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.nte.databank.coinbase_close_adapter import coinbase_nt_close_to_databank_raw
from trading_ai.nte.databank.trade_intelligence_databank import process_closed_trade
from trading_ai.core.capital_engine import CapitalEngine, capital_preflight_block
from trading_ai.core.position_engine import net_exit_from_fills, position_state_from_open_dict
from trading_ai.edge.execution_policy import edge_allowed_in_regime, resolve_coinbase_edge
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.nte.execution.product_rules import round_base_to_increment
from trading_ai.organism.trade_truth import (
    assert_no_oversell,
    assert_valid_base_quote,
    log_execution_truth_record,
)
from trading_ai.nte.research.research_firewall import live_routing_permitted
from trading_ai.nte.strategies.ab_router import RouterDecision, pick_live_route
from trading_ai.strategy.strategy_validation_engine import StrategyValidationEngine, strategy_preflight_ok

logger = logging.getLogger(__name__)


def _nte_entry_gates_coinbase(
    *,
    product_id: str,
    strategy_route_label: str,
    route_bucket: str,
) -> Tuple[bool, Optional[str], str]:
    """
    NTE Coinbase entry policy order (non-negotiable):

    1. Governance (joint review / enforcement) — first decisive gate.
    2. Strategy live-routing approval (research firewall) — downstream policy.

    Returns ``(proceed, failure_kind, detail)`` where ``failure_kind`` is
    ``\"governance\"``, ``\"strategy\"``, or ``None`` when ``proceed`` is True.
    """
    allowed, gov_reason, _audit = check_new_order_allowed_full(
        venue="coinbase",
        operation="nte_new_entry",
        route=str(strategy_route_label or "n/a"),
        intent_id=str(product_id or "n/a"),
        strategy_class=str(strategy_route_label or "n/a"),
        route_bucket=str(route_bucket or "n/a"),
        log_decision=True,
    )
    if not allowed:
        return False, "governance", gov_reason
    if not live_routing_permitted(strategy_route_label):
        return False, "strategy", str(strategy_route_label or "n/a")
    return True, None, "ok"


def _route_bucket_for_nte(dec: RouterDecision) -> str:
    """Stable router bucket label for governance metadata (not a safety input)."""
    rr = (dec.router_reason or "").strip() or "nte_router"
    vr = (dec.vol_regime or "").strip() or "unknown_vol"
    if len(rr) > 96:
        rr = rr[:96] + "…"
    return f"{vr}|{rr}"


def _hash_band(key: str, lo: float, hi: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    t = (h % 10001) / 10001.0
    return lo + t * (hi - lo)


def _fmt_base(sz: float, product_id: str) -> str:
    if "BTC" in product_id.upper():
        s = f"{sz:.8f}"
    else:
        s = f"{sz:.6f}"
    s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _fmt_limit_price(px: float, product_id: str) -> str:
    if "BTC" in product_id.upper():
        return f"{px:.2f}"
    return f"{px:.2f}"


def coinbase_nt_enabled() -> bool:
    return (os.environ.get("COINBASE_ENABLED") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _paper_mode() -> bool:
    return (os.environ.get("NTE_PAPER_MODE") or "").strip().lower() in ("1", "true", "yes")


def _use_limit_entries() -> bool:
    return (os.environ.get("NTE_USE_LIMIT_ENTRIES", "true")).strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _ws_enabled() -> bool:
    return (os.environ.get("NTE_WS_ENABLED", "true")).strip().lower() in ("1", "true", "yes")


class CoinbaseNTEngine:
    """BTC/ETH spot — maker limits for entry; market IOC for exits; WS + REST."""

    def __init__(
        self,
        client: Optional[Any] = None,
        settings: Optional[NTECoinbaseSettings] = None,
    ) -> None:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        self._client = client or CoinbaseClient()
        self.settings = settings or load_nte_settings()
        self.store = MemoryStore()
        self.store.ensure_defaults()
        self.state: Dict[str, Any] = load_state()
        self.rewards = RewardEngine(self.store)
        self.learning = GlobalLearningEngine(self.store)
        self.iteration = IterationEngine(self.store)
        self.goals = GoalEvaluator(self.store)
        self._realized_total = float(load_state().get("lifetime_realized_usd") or 0.0)
        self.launch = load_coinbase_avenue1_launch()
        self._strategy_validation = StrategyValidationEngine()
        self._ws_feed: Optional[AdvancedTradeWSFeed] = None
        if _ws_enabled():
            self._ws_feed = AdvancedTradeWSFeed(list(self.settings.products))
            try:
                self._ws_feed.start()
                logger.info("NTE: Advanced Trade WS feed starting")
            except Exception as exc:
                logger.warning("NTE: WS start failed (%s) — REST only", exc)

    # ── public API used by shark ────────────────────────────────────────────

    def load_state_and_reconcile(self) -> None:
        self.state = load_state()
        if not self.state.get("positions"):
            return
        try:
            for cur in ("BTC", "ETH"):
                float(self._client.get_available_balance(cur))
        except Exception as exc:
            logger.debug("reconcile balance check: %s", exc)

    def get_summary(self) -> Dict[str, Any]:
        self.state = load_state()
        self._realized_total = float(self.state.get("lifetime_realized_usd") or self._realized_total)
        pos = open_positions_list(self.state)
        pend = self._pending_list()
        by_product: Dict[str, int] = {}
        for p in pos:
            pid = str(p.get("product_id") or "")
            by_product[pid] = by_product.get(pid, 0) + 1
        return {
            "nte": True,
            "execution_model": "limit_entry_market_exit",
            "daily_pnl_usd": float(self.state.get("day_realized_pnl_usd") or 0.0),
            "total_realized_usd": self._realized_total,
            "open_count": len(pos),
            "pending_limits": len(pend),
            "by_product": by_product,
            "total_cost_usd": sum(float(p.get("buy_cost_usd") or 0) for p in pos),
        }

    def run_fast_tick(self) -> None:
        """~1–10s: exits + pending limit management (cancel stale)."""
        self._tick(entries=False, manage_pending=True)

    def run_slow_tick(self) -> None:
        """~5m: pending cleanup + new entries (limit or market fallback)."""
        self._tick(entries=True, manage_pending=True)

    def run_cycle(self) -> None:
        self.run_fast_tick()
        self.run_slow_tick()

    def run_exits_only(self) -> None:
        self.run_fast_tick()

    def dawn_sweep_gate_a(self) -> int:
        return 0

    # ── internals ───────────────────────────────────────────────────────────

    def _pending_list(self) -> List[Dict[str, Any]]:
        p = self.state.get("pending_entry_orders") or []
        return [x for x in p if isinstance(x, dict)]

    def _ws_bid_ask(self, product_id: str) -> Tuple[Optional[float], Optional[float]]:
        if not self._ws_feed:
            return None, None
        row = self._ws_feed.latest().get(product_id) or {}
        b, a = row.get("best_bid"), row.get("best_ask")
        try:
            if b is not None and a is not None and float(b) > 0 and float(a) > 0:
                return float(b), float(a)
        except (TypeError, ValueError):
            pass
        return None, None

    def _equity(self) -> float:
        usd = float(self._client.get_usd_balance())
        for p in open_positions_list(self.state):
            pid = str(p.get("product_id") or "")
            bid, ask = self._client.get_product_price(pid)
            wb, wa = self._ws_bid_ask(pid)
            if wb and wa:
                bid, ask = wb, wa
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            base = float(p.get("base_size") or 0)
            usd += base * mid
        return max(usd, 0.01)

    def _tick(self, *, entries: bool, manage_pending: bool) -> None:
        if not coinbase_nt_enabled():
            return
        self.state = load_state()
        self._realized_total = float(
            self.state.get("lifetime_realized_usd") or self._realized_total
        )
        reward = self.rewards.load()
        equity = self._equity()
        rv = risk_mod.evaluate_risk(
            state=self.state,
            equity=equity,
            settings=self.settings,
            reward=reward,
        )
        if entries:
            self.goals.update(equity=equity)

        if rv.paused:
            logger.info("NTE paused: %s", rv.reason)
            save_state(self.state)
            return

        for pos in list(open_positions_list(self.state)):
            self._maybe_exit(pos)

        if manage_pending:
            self._manage_pending_orders()

        if not entries:
            save_state(self.state)
            return

        if self._should_skip_entries_degraded():
            logger.info("NTE: new entries skipped (degraded / system health)")
            save_state(self.state)
            return

        gr = self.launch.global_risk
        if gr.launch_session_clamp and int(self.state.get("consecutive_losses") or 0) >= int(
            gr.clamp_pause_entries_after_consecutive_losses
        ):
            logger.info(
                "NTE: new entries skipped (launch clamp: consecutive losses >= %s)",
                gr.clamp_pause_entries_after_consecutive_losses,
            )
            save_state(self.state)
            return

        eff_max_open = int(self.settings.max_open_positions)
        if gr.launch_session_clamp:
            eff_max_open = min(eff_max_open, int(gr.clamp_max_open_positions))

        if len(open_positions_list(self.state)) >= eff_max_open:
            save_state(self.state)
            return

        occupied = {p.get("product_id") for p in open_positions_list(self.state)}
        occupied |= {p.get("product_id") for p in self._pending_list()}
        max_pend = int(self.launch.global_risk.max_pending_orders_total)
        if len(self._pending_list()) >= max_pend:
            logger.info("NTE: at max pending orders (%s)", max_pend)
            save_state(self.state)
            return
        for pid in self.launch.global_risk.products:
            if pid in occupied:
                continue
            self._maybe_enter(pid, rv.size_fraction, equity)
        save_state(self.state)

    def _should_skip_entries_degraded(self) -> bool:
        if not self.launch.global_risk.degraded_mode_blocks_entries:
            return False
        if self.launch.global_risk.max_new_entries_if_degraded > 0:
            return False
        p = nte_system_health_path()
        if not p.is_file():
            return False
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return False
        if h.get("healthy") is False:
            return True
        if h.get("execution_should_pause"):
            return True
        if h.get("global_pause"):
            return True
        dc = h.get("degraded_components") or []
        return isinstance(dc, list) and len(dc) > 0

    def _shadow_log(self, product_id: str, dec: Any, feat: Any) -> None:
        """Log live vs paper shadow row for learning (maker intent = limit path)."""
        try:
            p = self.store.path("shadow_compare_events.json")
            data: Dict[str, Any] = {"events": []}
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {"events": []}
            row = {
                "ts": time.time(),
                "product_id": product_id,
                "chosen": dec.chosen.name if dec.chosen else None,
                "rejected_routes": list(dec.rejected),
                "router_reason": dec.router_reason,
                "score_a": dec.score_a,
                "score_b": dec.score_b,
                "score_c": dec.score_c,
                "net_edge_bps_est": dec.net_edge_bps,
                "spread_bps": dec.spread_bps,
                "expected_move_bps_est": getattr(dec, "expected_move_bps", None),
                "est_round_trip_cost_bps": getattr(dec, "est_round_trip_cost_bps", None),
                "estimated_maker_fee_pct": getattr(dec, "estimated_maker_fee_pct", None),
                "estimated_taker_fee_pct": getattr(dec, "estimated_taker_fee_pct", None),
                "regime": feat.regime,
                "paper_would_note": "mirror_limit_entry_intent",
            }
            ev = list(data.get("events") or [])
            ev.append(row)
            data["events"] = ev[-1000:]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("shadow log: %s", exc)

    def _short_vol_bps(self, feat: Any, product_id: str) -> float:
        pms = ProductMarketState(product_id=product_id, best_bid=feat.bid, best_ask=feat.ask)
        pms.update_mid()
        mm = self.store.load_json("market_memory.json")
        closes = (mm.get("closes") or {}).get(product_id) or []
        if isinstance(closes, list):
            for c in closes[-80:]:
                try:
                    pms.recent_mids.append(float(c))
                except (TypeError, ValueError):
                    pass
        pms.update_mid()
        return float(pms.short_volatility_bps())

    def _regime_ok(self, feat: Any, product_id: str) -> bool:
        pms = ProductMarketState(product_id=product_id, best_bid=feat.bid, best_ask=feat.ask)
        pms.update_mid()
        mm = self.store.load_json("market_memory.json")
        closes = (mm.get("closes") or {}).get(product_id) or []
        if isinstance(closes, list):
            for c in closes[-80:]:
                try:
                    pms.recent_mids.append(float(c))
                except (TypeError, ValueError):
                    pass
        pms.update_mid()
        max_sp_bps = spread_cap_pct(product_id, self.launch) * 10000.0
        reg = classify_market(
            pms,
            max_spread_bps=float(max_sp_bps),
            max_volatility_bps=float(vol_cap_bps(product_id, self.launch)),
            spike_block_pct=float(self.settings.spike_block_pct),
        )
        if reg == "chaotic":
            logger.info("NTE skip %s: market regime chaotic", product_id)
            return False
        return True

    def _maybe_exit(self, pos: Dict[str, Any]) -> None:
        pid = str(pos.get("product_id") or "")
        wb, wa = self._ws_bid_ask(pid)
        bid, ask = self._client.get_product_price(pid)
        if wb and wa:
            bid, ask = wb, wa
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        entry = float(pos.get("entry_price") or 0)
        if entry <= 0 or mid <= 0:
            return
        pnl_pct = (mid - entry) / entry
        now = time.time()
        opened = float(pos.get("opened_ts") or now)
        tp = float(pos.get("tp_pct") or 0.007)
        sl = float(pos.get("sl_pct") or 0.006)
        tmax = float(pos.get("time_stop_sec") or 240)
        trail_lock = float(pos.get("trail_lock_pct") or 0.0025)
        if pnl_pct >= self.settings.trail_trigger:
            pos["trail_active"] = True
        exit_reason = None
        if pnl_pct >= tp:
            exit_reason = "take_profit"
        elif pnl_pct <= -sl:
            exit_reason = "stop_loss"
        elif now - opened >= tmax:
            exit_reason = "time_stop"
        elif pos.get("trail_active") and pnl_pct < trail_lock:
            exit_reason = "trailing_lock"
        if exit_reason:
            self._finalize_exit(pos, exit_reason)

    def _finalize_exit(self, pos: Dict[str, Any], reason: str) -> None:
        pid = str(pos.get("product_id") or "")
        base_pos = float(pos.get("base_size") or 0)
        if base_pos <= 0:
            self._remove_position(pos)
            return
        rounded_str = round_base_to_increment(pid, base_pos)
        try:
            rounded_float = float(rounded_str)
        except (TypeError, ValueError):
            rounded_float = base_pos
        sell_base = min(base_pos, rounded_float)
        if sell_base <= 1e-12:
            self._remove_position(pos)
            return
        assert_no_oversell(base_pos, sell_base)
        res = self._client.place_market_sell(pid, _fmt_base(sell_base, pid))
        if not getattr(res, "success", False):
            logger.warning("NTE exit sell failed %s: %s", pid, getattr(res, "reason", res))
            return
        time.sleep(1.2)
        oid = str(getattr(res, "order_id", "") or "")
        fills = self._client.get_fills(oid) if oid else []
        sell_usd = 0.0
        sell_fee = 0.0
        for f in fills:
            try:
                sell_usd += float(f.get("price") or 0) * float(f.get("size") or 0)
                sell_fee += abs(float(f.get("commission") or 0))
            except (TypeError, ValueError):
                continue
        buy_fee = float(pos.get("buy_fee_usd") or 0)
        pstate = position_state_from_open_dict(pos)
        net = net_exit_from_fills(
            pstate,
            sell_quote_notional=sell_usd,
            sell_fee=sell_fee,
            sold_base=sell_base,
        )
        entry_px = float(pos.get("entry_price") or 0)
        entry_notional = entry_px * sell_base
        gross_pnl = sell_usd - entry_notional
        sell_avg = sell_usd / sell_base if sell_base > 1e-12 else 0.0
        try:
            assert_valid_base_quote(sell_base, sell_usd, sell_avg)
        except ValueError as exc:
            logger.critical("post-sell base/quote invariant failed: %s", exc)
            try:
                from trading_ai.core.system_guard import get_system_guard

                get_system_guard().record_execution_anomaly("base_quote_invariant_exit")
            except Exception:
                logger.debug("system_guard anomaly hook skipped", exc_info=True)
        log_execution_truth_record(
            base_size=sell_base,
            quote_size=sell_usd,
            price=sell_avg,
            source="fill_parser",
        )

        equity_before = self._equity()
        risk_mod.register_closed_trade_pnl(self.state, net, equity_before, self.settings)
        self._realized_total += net
        self.state["lifetime_realized_usd"] = float(self._realized_total)

        mistake = None
        if reason == "stop_loss":
            mistake = "hit_stop"
        elif reason == "time_stop" and net < 0:
            mistake = "time_stop_loss"

        self.rewards.process_trade_outcome(
            net_pnl_usd=net,
            rule_adherent=mistake is None,
            mistake=mistake,
            strategy=str(pos.get("strategy") or "unknown"),
        )

        try:
            self._strategy_validation.record_trade(
                str(pos.get("strategy") or "unknown"),
                pnl=net,
                slippage=0.0,
                latency_ms=0.0,
                success=net > 0.0,
            )
        except Exception as exc:
            logger.debug("strategy validation record_trade: %s", exc)

        esp = float(pos.get("entry_spread_pct") or 0.0)
        exp_edge = float(pos.get("expected_edge_bps") or 0.0)
        entry_exec = str(pos.get("entry_execution") or "limit_gtc")
        realized_move_bps = None
        if entry_px > 0 and sell_avg > 0:
            realized_move_bps = (sell_avg - entry_px) / entry_px * 10000.0

        record = {
            "trade_id": str(pos.get("id") or ""),
            "avenue_id": "coinbase",
            "avenue": "coinbase",
            "product_id": pid,
            "asset": "BTC" if "BTC" in pid.upper() else ("ETH" if "ETH" in pid.upper() else pid),
            "execution_type": "exit_market_ioc",
            "setup_type": pos.get("strategy"),
            "entry_reason": pos.get("entry_reason"),
            "spread": esp,
            "spread_bps": esp * 10000.0,
            "volatility": pos.get("entry_z"),
            "regime": pos.get("entry_regime"),
            "duration_sec": time.time() - float(pos.get("opened_ts") or time.time()),
            "outcome": "win" if net > 0 else "loss",
            "fees": buy_fee + sell_fee,
            "fees_usd": buy_fee + sell_fee,
            "gross_pnl_usd": gross_pnl,
            "net_pnl_usd": net,
            "entry_price": entry_px,
            "exit_price": sell_avg,
            "base_size": sell_base,
            "expected_edge_bps": exp_edge,
            "entry_maker_intent": entry_exec == "limit_gtc",
            "entry_execution": entry_exec,
            "realized_move_bps": realized_move_bps,
            "router_score_a": pos.get("router_score_a"),
            "router_score_b": pos.get("router_score_b"),
            "execution_quality": "ok" if getattr(res, "success", False) else "error",
            "mistake_classification": mistake or "none",
            "exit_reason": reason,
            "hard_stop_exit": reason == "stop_loss",
        }
        self.learning.on_trade_closed(record)
        self.iteration.after_trade(record)

        try:
            raw_db = coinbase_nt_close_to_databank_raw(pos, record, exit_reason=reason)
            db_out = process_closed_trade(raw_db)
            logger.info(
                "NTE databank: trade_id=%s ok=%s",
                raw_db.get("trade_id"),
                db_out.get("ok"),
            )
        except Exception as exc:
            logger.warning("NTE databank pipeline (non-blocking): %s", exc)

        fees_total = buy_fee + sell_fee
        gross_trade = net + fees_total
        try:
            p = self.store.path("trade_truth_events.json")
            data: Dict[str, Any] = {"events": []}
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {"events": []}
            ev = list(data.get("events") or [])
            ev.append(
                {
                    "ts": time.time(),
                    "product_id": pid,
                    "phase": "exit_fill",
                    "strategy": pos.get("strategy"),
                    "expected_net_edge_bps": pos.get("expected_edge_bps"),
                    "est_round_trip_cost_bps": pos.get("est_round_trip_cost_bps"),
                    "router_score_a": pos.get("router_score_a"),
                    "router_score_b": pos.get("router_score_b"),
                    "realized_gross_pnl_usd": gross_trade,
                    "realized_fees_usd": fees_total,
                    "realized_net_pnl_usd": net,
                    "realized_move_bps": realized_move_bps,
                    "exit_reason": reason,
                }
            )
            data["events"] = ev[-1000:]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("trade_truth log: %s", exc)

        self._remove_position(pos)
        logger.info(
            "NTE closed %s net=$%.2f gross=$%.2f fees=$%.2f (%s)",
            pid,
            net,
            gross_trade,
            fees_total,
            reason,
        )

    def _remove_position(self, pos: Dict[str, Any]) -> None:
        pid = pos.get("id")
        self.state["positions"] = [p for p in (self.state.get("positions") or []) if p.get("id") != pid]

    def _remove_pending(self, client_key: str) -> None:
        self.state["pending_entry_orders"] = [
            p
            for p in (self.state.get("pending_entry_orders") or [])
            if p.get("client_key") != client_key
        ]

    def _manage_pending_orders(self) -> None:
        now = time.time()
        for pend in list(self._pending_list()):
            oid = str(pend.get("order_id") or "")
            placed = float(pend.get("placed_ts") or 0)
            if not oid:
                self._remove_pending(str(pend.get("client_key")))
                continue
            order = self._client.get_order(oid)
            if not isinstance(order, dict):
                continue
            st = str(order.get("status") or order.get("order_status") or "").upper()
            filled = False
            try:
                fsz = float(order.get("filled_size") or order.get("filled_quantity") or 0)
                tot = float(pend.get("base_size") or 0)
                if tot > 0 and fsz >= tot * 0.999:
                    filled = True
            except (TypeError, ValueError):
                pass
            if "FILLED" in st or filled:
                self._promote_pending_to_position(pend, oid)
                continue
            done = st in ("FILLED", "DONE", "CANCELLED", "EXPIRED", "FAILED")
            if done and not filled:
                self._remove_pending(str(pend.get("client_key")))
                continue
            stale_lim = float(pend.get("stale_sec") or self.settings.stale_pending_order_sec)
            if now - placed > stale_lim:
                if not _paper_mode():
                    self._client.cancel_order(oid)
                logger.info("NTE cancelled stale limit %s", oid[:12])
                try:
                    bump_exec_counter("stale_pending_canceled")
                except Exception:
                    pass
                self._remove_pending(str(pend.get("client_key")))

    def _promote_pending_to_position(self, pend: Dict[str, Any], order_id: str) -> None:
        fills = self._client.get_fills(order_id)
        base_sz, avg_px, buy_fee = self._fills_to_stats(fills)
        if base_sz <= 0 or avg_px <= 0:
            logger.warning("NTE promote pending: no fills for %s", order_id)
            self._remove_pending(str(pend.get("client_key")))
            return
        pid = str(pend.get("product_id") or "")
        buy_cost = base_sz * avg_px + buy_fee
        pos_id = new_position_id()
        tp = _hash_band(f"{pid}:{pos_id}:tp", self.settings.tp_min, self.settings.tp_max)
        sl = _hash_band(f"{pid}:{pos_id}:sl", self.settings.sl_min, self.settings.sl_max)
        ts_sec = int(
            _hash_band(
                f"{pid}:{pos_id}:ts",
                float(self.settings.time_stop_min_sec),
                float(self.settings.time_stop_max_sec),
            )
        )
        tlock = _hash_band(
            f"{pid}:{pos_id}:tl",
            self.settings.trail_lock_min,
            self.settings.trail_lock_max,
        )
        pos = {
            "id": pos_id,
            "product_id": pid,
            "base_size": base_sz,
            "entry_price": avg_px,
            "buy_cost_usd": buy_cost,
            "buy_fee_usd": buy_fee,
            "opened_ts": time.time(),
            "tp_pct": tp,
            "sl_pct": sl,
            "time_stop_sec": float(ts_sec),
            "trail_lock_pct": tlock,
            "trail_active": False,
            "strategy": pend.get("strategy"),
            "entry_reason": pend.get("entry_reason"),
            "entry_spread_pct": pend.get("entry_spread_pct"),
            "entry_z": pend.get("entry_z"),
            "entry_regime": pend.get("entry_regime"),
            "entry_execution": "limit_gtc",
            "expected_edge_bps": float(pend.get("net_edge_bps") or 0.0),
            "est_round_trip_cost_bps": pend.get("est_round_trip_cost_bps"),
            "estimated_maker_fee_pct": pend.get("estimated_maker_fee_pct"),
            "estimated_taker_fee_pct": pend.get("estimated_taker_fee_pct"),
            "expected_move_bps": pend.get("expected_move_bps"),
            "router_score_a": pend.get("router_score_a"),
            "router_score_b": pend.get("router_score_b"),
            "rejected_routes": pend.get("rejected_routes"),
            "router_reason": pend.get("router_reason"),
            "edge_id": pend.get("edge_id"),
            "edge_lane": pend.get("edge_lane"),
            "market_snapshot": pend.get("market_snapshot"),
        }
        self.state.setdefault("positions", []).append(pos)
        self._remove_pending(str(pend.get("client_key")))
        try:
            bump_exec_counter("limit_entries_filled")
        except Exception:
            pass
        logger.info("NTE limit filled → position %s size=%.6f", pid, base_sz)

    def _fills_to_stats(self, fills: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        base = 0.0
        cost = 0.0
        fee = 0.0
        for f in fills:
            try:
                sz = float(f.get("size") or 0)
                px = float(f.get("price") or 0)
                base += sz
                cost += px * sz
                fee += abs(float(f.get("commission") or 0))
            except (TypeError, ValueError):
                continue
        avg = cost / base if base > 0 else 0.0
        return base, avg, fee

    def _maybe_enter(self, product_id: str, size_fraction: float, equity: float) -> None:
        wb, wa = self._ws_bid_ask(product_id)
        feat = compute_features(
            store=self.store,
            client=self._client,
            product_id=product_id,
            spike_block_pct=self.settings.spike_block_pct,
            min_quote_volume_24h=self.settings.min_quote_volume_24h,
            bid_override=wb,
            ask_override=wa,
        )
        if feat is None:
            return
        cap_spread = spread_cap_pct(product_id, self.launch)
        if feat.spread_pct > cap_spread:
            return
        if not feat.stable:
            return
        if not self._regime_ok(feat, product_id):
            return

        sv = self._short_vol_bps(feat, product_id)
        dec = pick_live_route(feat, self.store, self.launch, short_vol_bps=sv)
        if dec is None or dec.chosen is None:
            return
        sig = dec.chosen
        rb = _route_bucket_for_nte(dec)
        ok_gate, fail_kind, gate_detail = _nte_entry_gates_coinbase(
            product_id=product_id,
            strategy_route_label=str(sig.name or "n/a"),
            route_bucket=rb,
        )
        if not ok_gate:
            if fail_kind == "governance":
                logger.info("NTE skip entry: governance gate (%s)", gate_detail)
            else:
                logger.info(
                    "NTE skip entry: strategy not approved for live routing (%s)",
                    gate_detail,
                )
            return

        if not strategy_preflight_ok(self._strategy_validation, str(sig.name or "unknown")):
            logger.info("NTE skip entry: strategy validation disabled (%s)", sig.name)
            return

        edge_asg = resolve_coinbase_edge(str(sig.name or "n/a"), product_id)
        if edge_asg.size_scale <= 0:
            logger.info("NTE skip entry: edge policy blocks scale (%s)", edge_asg.detail)
            return

        reg0 = str(getattr(feat, "regime", None) or "")
        if edge_asg.edge_id:
            er = EdgeRegistry().get(edge_asg.edge_id)
            if er is not None and isinstance(er.required_conditions, dict):
                tags = er.required_conditions.get("regime_tags")
                allowed_r = edge_allowed_in_regime(tags, reg0)
                logger.info(
                    "edge_regime_gate %s",
                    json.dumps(
                        {"edge_id": edge_asg.edge_id, "regime": reg0, "allowed": allowed_r},
                        default=str,
                    ),
                )
                if not allowed_r:
                    logger.info("NTE skip entry: regime_mismatch edge=%s", edge_asg.edge_id)
                    return

        market_snap = {
            "product_id": product_id,
            "regime": getattr(feat, "regime", None),
            "spread_pct": getattr(feat, "spread_pct", None),
            "mid": getattr(feat, "mid", None),
            "z_score": getattr(feat, "z_score", None),
        }

        if self.launch.global_risk.shadow_compare_enabled:
            self._shadow_log(product_id, dec, feat)

        usd = max(10.0, equity * float(dec.position_base_pct))
        usd = min(usd, equity * float(dec.position_max_pct), float(self._client.get_usd_balance()) * 0.98)
        usd = min(usd, equity * size_fraction * 1.25)
        usd *= float(edge_asg.size_scale)
        gr = self.launch.global_risk
        if gr.launch_session_clamp:
            usd = min(usd, equity * float(gr.clamp_equity_per_trade_pct_max))
        cap_sz = CapitalEngine.get_trade_size(equity)
        usd = min(usd, cap_sz)
        usd *= float(self._strategy_validation.priority_multiplier(str(sig.name or "unknown")))
        open_exp = sum(float(p.get("buy_cost_usd") or 0.0) for p in open_positions_list(self.state))
        day_pnl = float(self.state.get("day_realized_pnl_usd") or 0.0)
        day_start = float(self.state.get("day_start_equity") or equity)
        if day_start <= 0:
            day_start = equity
        blocked_ce, cr = capital_preflight_block(
            proposed_trade_usd=usd,
            account_balance_usd=equity,
            open_exposure_usd=open_exp,
            daily_pnl_usd=day_pnl,
            day_start_balance_usd=day_start,
        )
        if blocked_ce:
            logger.info("NTE skip entry: capital preflight (%s)", cr)
            return
        if usd < 10.0:
            return

        mid = feat.mid
        off = float(dec.strategy_params.get("entry_offset_bps", self.settings.entry_limit_offset_bps)) / 10000.0
        limit_px = mid * (1.0 - off)
        if limit_px >= feat.ask:
            limit_px = feat.bid * (1.0 - off * 0.5) if feat.bid > 0 else mid * (1.0 - off)
        base_sz = usd / max(limit_px, 1e-12)
        base_str = _fmt_base(base_sz, product_id)
        lim_str = _fmt_limit_price(limit_px, product_id)

        if _paper_mode():
            logger.info(
                "NTE PAPER limit BUY %s size=%s @ %s (strategy=%s)",
                product_id,
                base_str,
                lim_str,
                sig.name,
            )
            return

        if _use_limit_entries():
            res = self._place_limit_buy(product_id, base_str, lim_str)
            if not getattr(res, "success", False):
                logger.warning("NTE limit buy failed %s", getattr(res, "reason", res))
                return
            oid = str(getattr(res, "order_id", "") or "")
            ck = new_position_id()
            self.state.setdefault("pending_entry_orders", []).append(
                {
                    "client_key": ck,
                    "order_id": oid,
                    "product_id": product_id,
                    "placed_ts": time.time(),
                    "limit_price": float(limit_px),
                    "base_size": float(base_sz),
                    "strategy": sig.name,
                    "entry_reason": sig.reason,
                    "entry_spread_pct": feat.spread_pct,
                    "entry_z": feat.z_score,
                    "entry_regime": feat.regime,
                    "stale_sec": float(dec.stale_pending_seconds),
                    "router_score_a": dec.score_a,
                    "router_score_b": dec.score_b,
                    "net_edge_bps": dec.net_edge_bps,
                    "est_round_trip_cost_bps": dec.est_round_trip_cost_bps,
                    "estimated_maker_fee_pct": dec.estimated_maker_fee_pct,
                    "estimated_taker_fee_pct": dec.estimated_taker_fee_pct,
                    "expected_move_bps": dec.expected_move_bps,
                    "rejected_routes": list(dec.rejected),
                    "router_reason": dec.router_reason,
                    "edge_id": edge_asg.edge_id,
                    "edge_lane": edge_asg.edge_lane,
                    "market_snapshot": market_snap,
                }
            )
            try:
                bump_exec_counter("limit_entries_placed")
            except Exception:
                pass
            logger.info("NTE limit BUY placed %s @ %s oid=%s", product_id, lim_str, oid[:16])
            return

        res = self._client.place_market_buy(product_id, usd)
        if not getattr(res, "success", False):
            logger.warning("NTE market buy failed %s: %s", product_id, getattr(res, "reason", res))
            return
        time.sleep(1.5)
        roid = str(getattr(res, "order_id", "") or "")
        bsz, apx, bfee = self._buy_fill_stats(roid)
        if bsz <= 0 or apx <= 0:
            logger.warning("NTE could not resolve market buy fill for %s", product_id)
            return
        self._append_filled_position(
            product_id,
            bsz,
            apx,
            bfee,
            sig,
            feat,
            execution="market_ioc",
            router_decision=dec,
            edge_id=edge_asg.edge_id,
            edge_lane=edge_asg.edge_lane,
            market_snapshot=market_snap,
        )

    def _place_limit_buy(self, product_id: str, base_str: str, lim_str: str) -> Any:
        po = bool(self.settings.post_only_limits)
        res = self._client.place_limit_gtc(product_id, "BUY", base_str, lim_str, post_only=po)
        if getattr(res, "success", False):
            return res
        if po:
            logger.info("NTE retry limit BUY without post_only")
            return self._client.place_limit_gtc(product_id, "BUY", base_str, lim_str, post_only=False)
        return res

    def _append_filled_position(
        self,
        product_id: str,
        base_sz: float,
        avg_px: float,
        buy_fee: float,
        sig: Any,
        feat: Any,
        *,
        execution: str,
        router_decision: Optional[Any] = None,
        edge_id: Optional[str] = None,
        edge_lane: Optional[str] = None,
        market_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        buy_cost = base_sz * avg_px + buy_fee
        pos_id = new_position_id()
        tp = _hash_band(f"{product_id}:{pos_id}:tp", self.settings.tp_min, self.settings.tp_max)
        sl = _hash_band(f"{product_id}:{pos_id}:sl", self.settings.sl_min, self.settings.sl_max)
        ts_sec = int(
            _hash_band(
                f"{product_id}:{pos_id}:ts",
                float(self.settings.time_stop_min_sec),
                float(self.settings.time_stop_max_sec),
            )
        )
        tlock = _hash_band(
            f"{product_id}:{pos_id}:tl",
            self.settings.trail_lock_min,
            self.settings.trail_lock_max,
        )
        ne_bps = 0.0
        ra = None
        rb = None
        if router_decision is not None:
            try:
                ne_bps = float(router_decision.net_edge_bps)
                ra = router_decision.score_a
                rb = router_decision.score_b
            except Exception:
                pass
        pos = {
            "id": pos_id,
            "product_id": product_id,
            "base_size": base_sz,
            "entry_price": avg_px,
            "buy_cost_usd": buy_cost,
            "buy_fee_usd": buy_fee,
            "opened_ts": time.time(),
            "tp_pct": tp,
            "sl_pct": sl,
            "time_stop_sec": float(ts_sec),
            "trail_lock_pct": tlock,
            "trail_active": False,
            "strategy": sig.name,
            "entry_reason": sig.reason,
            "entry_spread_pct": feat.spread_pct,
            "entry_z": feat.z_score,
            "entry_regime": feat.regime,
            "entry_execution": execution,
            "expected_edge_bps": ne_bps,
            "router_score_a": ra,
            "router_score_b": rb,
            "edge_id": edge_id,
            "edge_lane": edge_lane,
            "market_snapshot": market_snapshot
            or {
                "product_id": product_id,
                "regime": getattr(feat, "regime", None),
                "spread_pct": getattr(feat, "spread_pct", None),
                "mid": getattr(feat, "mid", None),
                "z_score": getattr(feat, "z_score", None),
            },
        }
        self.state.setdefault("positions", []).append(pos)
        if execution == "market_ioc":
            try:
                bump_exec_counter("market_entries")
            except Exception:
                pass
        logger.info("NTE opened %s %s exec=%s size=%.6f", sig.name, product_id, execution, base_sz)

    def _buy_fill_stats(self, order_id: str) -> Tuple[float, float, float]:
        fills = self._client.get_fills(order_id) if order_id else []
        return self._fills_to_stats(fills)
