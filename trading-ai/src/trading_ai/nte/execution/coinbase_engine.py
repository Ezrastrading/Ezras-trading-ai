"""
Coinbase **Avenue A — NexTrading Engine (NTE)**.

Legacy gate/swing execution (Gates A–C) has been removed. All Coinbase logic lives in
``trading_ai.nte`` (execution, memory, learning, CEO, rewards, goals, research).

State: ``shark/state/nte_coinbase_positions.json``; memory: ``shark/nte/memory/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add src directory to Python path for module imports
src_path = Path(__file__).resolve().parents[3]
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from trading_ai.nte.execution.coinbase_sizing import (
    _PRODUCT_BASE_PRECISION,
    _enforce_min_base_for_sell,
    _fmt_base_size,
    _min_base_size_for_product,
)
from trading_ai.nte.config.coinbase_avenue1_launch import (
    CoinbaseAvenue1Launch,
    entry_offset_bps,
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
from trading_ai.edge.execution_queue import sort_key_for_nt_product
from trading_ai.latency.latency_engine import (
    LATENCY_MAX_HOLD_SECONDS,
    build_market_snapshot_for_latency,
    detect_latency_signal,
)
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.nte.execution.product_rules import round_base_to_increment
from trading_ai.organism.deployment_guard import (
    DeploymentGuard,
    FillSnapshot,
    PositionSnapshot,
    assert_position_reconciled_flat,
    assert_system_not_halted,
    assert_trade_lifecycle_complete,
    deployment_enforcement_enabled,
)
from trading_ai.organism.trade_truth import (
    assert_no_oversell,
    assert_valid_base_quote,
    log_execution_truth_record,
)
from trading_ai.nte.research.research_firewall import live_routing_permitted
from trading_ai.nte.strategies.ab_router import RouterDecision, pick_live_route
from trading_ai.nte.reality_lock import (
    FillMismatchAbort,
    aggregate_fills_to_stats,
    assert_no_oversell_strict,
    base_currency_for_product,
    capital_velocity_allows_trade,
    check_market_reality_pre_trade,
    fee_dominance_from_dec,
    get_venue_state,
    reality_halt,
    reconcile_coinbase_spot_base,
    record_trade_executed,
    wait_for_fill,
)
from trading_ai.strategy.strategy_validation_engine import StrategyValidationEngine, strategy_preflight_ok

from trading_ai.global_layer.gap_engine import (
    coinbase_liquidity_score,
    evaluate_candidate,
    map_coinbase_execution_mode,
    map_coinbase_gap_type,
)
from trading_ai.global_layer.gap_models import (
    UniversalGapCandidate,
    authoritative_live_buy_path_reset,
    authoritative_live_buy_path_set,
    candidate_context_reset,
    candidate_context_set,
    new_universal_candidate_id,
)
from trading_ai.global_layer.trade_snapshots import (
    write_edge_snapshot,
    write_execution_snapshot,
    write_master_snapshot,
    write_review_snapshot,
)
from trading_ai.global_layer.execution_grader import grade_execution
from trading_ai.global_layer.universal_sizing import size_from_candidate

logger = logging.getLogger(__name__)

_REALITY_ORDER_WAIT_SEC = float((os.environ.get("REALITY_LOCK_ORDER_WAIT_SEC") or "120").strip() or "120")
_REALITY_ORDER_TS_MAX_AGE = float((os.environ.get("REALITY_LOCK_MAX_ORDER_API_AGE_SEC") or "7200").strip() or "7200")


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def _cap_notional(
    *,
    proposed_usd: float,
    equity_usd: float,
    open_avenue_exposure_usd: float,
    open_gate_exposure_usd: float,
) -> float:
    """
    Hard caps (fail-closed):
    - per-trade
    - per-gate exposure
    - per-avenue exposure
    """
    try:
        p = float(proposed_usd)
        eq = float(equity_usd)
        oa = float(open_avenue_exposure_usd)
        og = float(open_gate_exposure_usd)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0 or eq <= 0:
        return 0.0

    per_trade_pct = _env_float("EZRAS_CAP_PER_TRADE_PCT", 0.10)
    per_gate_pct = _env_float("EZRAS_CAP_PER_GATE_PCT", 0.20)
    per_avenue_pct = _env_float("EZRAS_CAP_PER_AVENUE_PCT", 0.30)

    per_trade_usd = _env_float("EZRAS_CAP_PER_TRADE_USD", eq * per_trade_pct)
    per_gate_usd = _env_float("EZRAS_CAP_PER_GATE_USD", eq * per_gate_pct)
    per_avenue_usd = _env_float("EZRAS_CAP_PER_AVENUE_USD", eq * per_avenue_pct)

    p = min(p, max(0.0, per_trade_usd))
    rem_gate = max(0.0, float(per_gate_usd) - og)
    rem_avenue = max(0.0, float(per_avenue_usd) - oa)
    p = min(p, rem_gate, rem_avenue)
    return max(0.0, p)


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
    from trading_ai.nte.hardening.mode_context import coinbase_avenue_execution_enabled

    return coinbase_avenue_execution_enabled()


def _paper_mode() -> bool:
    return (os.environ.get("NTE_PAPER_MODE") or "").strip().lower() in ("1", "true", "yes")


def _use_limit_entries() -> bool:
    return (os.environ.get("NTE_USE_LIMIT_ENTRIES", "true")).strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _strict_execution_mode() -> bool:
    # Default strict (fail-closed): no order-type fallbacks or degraded behavior.
    return (os.environ.get("EZRAS_STRICT_EXECUTION_MODE") or "true").strip().lower() in ("1", "true", "yes")


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
        self._adaptive_size_mult: float = 1.0
        self._last_adaptive_gate: Optional[Dict[str, Any]] = None
        self._last_gate_allocation: Dict[str, Any] = {}
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

        try:
            from trading_ai.control.adaptive_routing_live import compute_live_gate_allocation
            from trading_ai.control.live_adaptive_integration import coinbase_entry_adaptive_gate

            peak = float(self.state.get("session_peak_equity_usd") or 0.0)
            if equity > peak:
                self.state["session_peak_equity_usd"] = equity
                peak = equity
            roll_hi = max(peak, equity, 1.0)
            mr = str(self.state.get("nte_last_regime") or "neutral")
            chop = float(self.state.get("nte_last_chop_score") or 0.35)
            slip_h = float(self.state.get("adaptive_slippage_health_hint") or 0.85)
            liq_h = float(self.state.get("adaptive_liquidity_health_hint") or 0.85)
            pid_hint = None
            try:
                prods = list(getattr(self.settings, "products", None) or [])
                pid_hint = str(prods[0]).strip() if prods else None
            except (IndexError, TypeError, ValueError):
                pid_hint = None
            ag = coinbase_entry_adaptive_gate(
                equity=equity,
                rolling_equity_high=roll_hi,
                market_regime=mr,
                market_chop_score=chop,
                slippage_health=slip_h,
                liquidity_health=liq_h,
                product_id=pid_hint,
            )
            self._last_adaptive_gate = ag
            self._adaptive_size_mult = float(ag.get("size_multiplier") or 1.0)
            rep = (ag.get("proof") or {}).get("report") or {}
            self._last_gate_allocation = compute_live_gate_allocation(
                aos_report=rep,
                market_quality_allows_adaptive=bool(rep.get("confidence_scaling_ready") or True),
                entrypoint="nte_execution_coinbase_engine",
                route="avenue_a_entry_cycle",
                venue="coinbase",
                product_id=pid_hint,
            )
            logger.info(
                "NTE adaptive OS evaluated mode=%s block_entries=%s size_mult=%s routing=%s",
                ag.get("mode"),
                ag.get("block_new_entries"),
                self._adaptive_size_mult,
                self._last_gate_allocation.get("route_source"),
            )
            if ag.get("block_new_entries"):
                logger.info("NTE: new entries blocked by adaptive operating system")
                save_state(self.state)
                return
        except Exception as exc:
            logger.warning("NTE adaptive operating system unavailable (%s) — continuing with mult=1.0", exc)
            self._adaptive_size_mult = 1.0
            self._last_adaptive_gate = {
                "block_new_entries": False,
                "size_multiplier": 1.0,
                "adaptive_os_evaluated": False,
                "error": str(exc),
            }

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

        n_open = len(open_positions_list(self.state))
        base_cap = eff_max_open
        try:
            from trading_ai.risk.position_control import effective_max_open_positions, position_cap_blocks_new_entry

            eff_max_open = effective_max_open_positions(base_cap)
            blocked, reason = position_cap_blocks_new_entry(n_open, base_cap)
        except Exception:
            blocked = n_open >= eff_max_open
            reason = ""
        if blocked:
            if reason == "bootstrap_position_cap":
                try:
                    from trading_ai.control.alerts import emit_alert

                    emit_alert("INFO", "Position cap active")
                except Exception:
                    pass
            save_state(self.state)
            return

        occupied = {p.get("product_id") for p in open_positions_list(self.state)}
        occupied |= {p.get("product_id") for p in self._pending_list()}
        max_pend = int(self.launch.global_risk.max_pending_orders_total)
        if len(self._pending_list()) >= max_pend:
            logger.info("NTE: at max pending orders (%s)", max_pend)
            save_state(self.state)
            return
        pids = [p for p in self.launch.global_risk.products if p not in occupied]
        ranked: List[Tuple[str, Tuple[int, float]]] = []
        for pid in pids:
            ranked.append((pid, self._entry_sort_key(pid)))
        ranked.sort(key=lambda x: x[1])
        for pid, _sk in ranked:
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
        if deployment_enforcement_enabled():
            assert_system_not_halted()
        rounded_str = round_base_to_increment(pid, base_pos)
        try:
            rounded_float = float(rounded_str)
        except (TypeError, ValueError):
            rounded_float = base_pos
        sell_base = min(base_pos, rounded_float)
        if sell_base <= 1e-12:
            self._remove_position(pos)
            return
        assert_no_oversell_strict(base_pos, sell_base)
        assert_no_oversell(base_pos, sell_base)
        if deployment_enforcement_enabled():
            DeploymentGuard().validate_post_sell(PositionSnapshot(base_size=base_pos), sell_base)
        res = self._client.place_market_sell(pid, _fmt_base(sell_base, pid))
        if not getattr(res, "success", False):
            logger.warning("NTE exit sell failed %s: %s", pid, getattr(res, "reason", res))
            return
        oid = str(getattr(res, "order_id", "") or "")
        fills: List[Dict[str, Any]] = []
        try:
            fills = wait_for_fill(
                self._client,
                oid,
                max_wait_sec=_REALITY_ORDER_WAIT_SEC,
                max_stale_order_age_sec=_REALITY_ORDER_TS_MAX_AGE,
            )
        except Exception as exc:
            reality_halt(str(exc))
            raise
        filled_base, sell_avg, sell_fee = self._fills_to_stats(fills)
        if filled_base <= 1e-12:
            logger.warning("NTE exit: no normalized fill base for %s", pid)
            return
        sell_base = min(sell_base, filled_base)
        sell_usd = sell_base * sell_avg
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
        if deployment_enforcement_enabled():
            DeploymentGuard().validate_round_trip(
                FillSnapshot(
                    base_size=base_pos,
                    quote_size=base_pos * entry_px if entry_px > 0 else sell_usd,
                    price=entry_px if entry_px > 0 else sell_avg,
                ),
                FillSnapshot(base_size=sell_base, quote_size=sell_usd, price=sell_avg),
            )
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
        # Canonical snapshots (exit + grade + review). Best-effort; first-20 validator checks integrity.
        try:
            rr = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
            runtime_root = rr if str(rr) else None
        except Exception:
            runtime_root = None
        try:
            cand_id = str(pos.get("candidate_id") or "")
            intended_mode = "maker" if str(pos.get("entry_execution") or "") in ("limit_gtc", "limit_gtc_post_only") else "taker"
            g = grade_execution(
                side="BUY",
                intended_execution_mode=intended_mode,
                actual_execution_mode=intended_mode,
                intended_price=float(pos.get("limit_price") or pos.get("entry_price") or 0.0),
                actual_fill_price=float(pos.get("entry_price") or 0.0),
                slippage_estimate_usd=None,
                slippage_actual_usd=None,
                fees_estimate_usd=None,
                fees_actual_usd=float(buy_fee + sell_fee),
            )
            write_execution_snapshot(
                runtime_root,
                {
                    "trade_id": str(pos.get("id") or ""),
                    "order_type_entry": str(pos.get("entry_execution") or ""),
                    "order_type_exit": "market_ioc",
                    "intended_execution_mode": intended_mode,
                    "actual_execution_mode": intended_mode,
                    "posted_limit_price": pos.get("limit_price"),
                    "actual_fill_price": float(pos.get("entry_price") or 0.0),
                    "maker_or_taker_entry": "maker" if intended_mode == "maker" else "taker",
                    "maker_or_taker_exit": "taker",
                    "execution_grade": g.grade,
                    "execution_grade_reasons": g.reasons,
                    "fees_actual": float(buy_fee + sell_fee),
                    "actual_exit_fill_price": float(sell_avg),
                },
            )
            write_review_snapshot(
                runtime_root,
                {
                    "trade_id": str(pos.get("id") or ""),
                    "expected_value_pretrade": None,
                    "net_pnl": float(net),
                    "anomaly_flags": [],
                    "should_repeat": None,
                    "should_reduce": None,
                    "should_pause": None,
                    "notes": {"candidate_id": cand_id, "execution_grade": g.grade, "execution_grade_reasons": g.reasons},
                },
            )
        except Exception:
            pass
        self.learning.on_trade_closed(record)
        self.iteration.after_trade(record)

        db_out: Dict[str, Any] = {}
        try:
            raw_db = coinbase_nt_close_to_databank_raw(pos, record, exit_reason=reason)
            db_out = process_closed_trade(raw_db)
            logger.info(
                "NTE databank: trade_id=%s ok=%s",
                raw_db.get("trade_id"),
                db_out.get("ok"),
            )
        except Exception as exc:
            if deployment_enforcement_enabled():
                raise
            logger.warning("NTE databank pipeline (non-blocking): %s", exc)
        if deployment_enforcement_enabled():
            try:
                assert_position_reconciled_flat(client=self._client, product_id=pid)
            except Exception as _recon_exc:
                try:
                    from trading_ai.control.alerts import emit_alert

                    emit_alert("CRITICAL", f"reconciliation_mismatch: {_recon_exc!s}")
                except Exception:
                    pass
                raise
            assert_trade_lifecycle_complete(
                buy_success=True,
                sell_success=bool(getattr(res, "success", False) and sell_base > 0),
                pnl_computed=True,
                supabase_written=bool(db_out.get("ok")),
            )

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

        # Snapshots: execution + master exit fields + review grade (fail-closed truth)
        try:
            from trading_ai.runtime.trade_snapshots import (
                SnapshotWriteError,
                enforce_snapshot_integrity,
                mark_trade_invalid,
                snapshot_trades_execution,
                snapshot_trades_master,
                snapshot_trades_review,
            )

            tid = str(pos.get("trade_id") or pos.get("id") or "").strip() or str(pos.get("id") or "")
            ex_wr = snapshot_trades_execution(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                execution_snapshot={
                    "phase": "exit_fill",
                    "exit_reason": str(reason),
                    "exit_order_id": str(oid),
                    "sold_base": float(sell_base),
                    "exit_avg_price": float(sell_avg),
                    "sell_fee_usd": float(sell_fee),
                    "buy_fee_usd": float(buy_fee),
                    "fees_total_usd": float(fees_total),
                    "gross_pnl_usd": float(gross_pnl),
                    "net_pnl_usd": float(net),
                    "entry_price": float(entry_px),
                    "exit_price": float(sell_avg),
                    "realized_move_bps": realized_move_bps,
                },
            )
            ms_wr = snapshot_trades_master(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                payload={
                    "trade_id": tid,
                    "timestamp_close": datetime.now(timezone.utc).isoformat(),
                    "exit_reason": str(reason),
                    "exit_price": float(sell_avg),
                    "net_pnl_usd": float(net),
                    "gross_pnl_usd": float(gross_pnl),
                    "fees_usd": float(fees_total),
                    "truth_valid": True,
                    "gap_type": str(pos.get("gap_type") or ""),  # must be present for truth-joined reports
                },
            )
            rv_wr = snapshot_trades_review(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                review_snapshot={
                    "phase": "post_close",
                    "execution_grade": (record.get("execution_grade") or record.get("execution_quality")),
                    "mistake_classification": record.get("mistake_classification"),
                    "exit_reason": str(reason),
                    "net_pnl": float(net),
                    "gap_type": str(pos.get("gap_type") or ""),
                },
            )
            enforce_snapshot_integrity(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                edge_ok=True,
                execution_ok=bool(getattr(ex_wr, "ok", False)),
                review_ok=bool(getattr(rv_wr, "ok", False)),
            )
        except SnapshotWriteError as exc:
            mark_trade_invalid(record, reason=str(exc))
            logger.critical("NTE snapshot failure on close; marking trade invalid (%s)", exc)
            # Hard fail: trade must not be counted as valid in any readiness chain.
            raise

        self._remove_position(pos)
        try:
            bcc = base_currency_for_product(pid)
            internal_sum = sum(
                float(p.get("base_size") or 0)
                for p in open_positions_list(self.state)
                if base_currency_for_product(str(p.get("product_id") or "")) == bcc
            )
            reconcile_coinbase_spot_base(self._client, bcc, internal_sum)
        except Exception as exc:
            reality_halt(str(exc))
            raise
        try:
            record_trade_executed(
                notional_usd=max(float(pos.get("buy_cost_usd") or 0.0), sell_usd),
                net_pnl_usd=float(net),
            )
        except Exception as exc:
            logger.debug("record_trade_executed: %s", exc)
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
        from trading_ai.runtime.trade_snapshots import SnapshotWriteError, mark_trade_invalid, snapshot_trades_execution, snapshot_trades_master

        fills = self._client.get_fills(order_id)
        base_sz, avg_px, buy_fee = self._fills_to_stats(fills)
        if base_sz <= 0 or avg_px <= 0:
            logger.warning("NTE promote pending: no fills for %s", order_id)
            self._remove_pending(str(pend.get("client_key")))
            return
        if deployment_enforcement_enabled():
            DeploymentGuard().validate_post_buy(
                FillSnapshot(
                    base_size=base_sz,
                    quote_size=base_sz * avg_px,
                    price=avg_px,
                )
            )
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
        if bool(pend.get("latency_fast_exit")):
            ts_sec = int(min(float(ts_sec), float(LATENCY_MAX_HOLD_SECONDS)))
        tlock = _hash_band(
            f"{pid}:{pos_id}:tl",
            self.settings.trail_lock_min,
            self.settings.trail_lock_max,
        )
        pos = {
            "id": pos_id,
            "trade_id": pend.get("trade_id") or pos_id,
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
            "latency_trade": bool(pend.get("latency_trade")),
        }
        self.state.setdefault("positions", []).append(pos)
        self._remove_pending(str(pend.get("client_key")))

        tid = str(pos.get("trade_id") or pos_id).strip() or str(pos_id)
        try:
            snapshot_trades_execution(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                execution_snapshot={
                    "phase": "entry_fill",
                    "order_id": str(order_id),
                    "filled_base": float(base_sz),
                    "avg_price": float(avg_px),
                    "fee_usd": float(buy_fee),
                    "quote_usd": float(buy_cost),
                    "fills_count": len(fills or []),
                    "execution_type": "limit_gtc",
                },
            )
            snapshot_trades_master(
                trade_id=tid,
                avenue_id="coinbase",
                gate_id="gate_a",
                payload={
                    "trade_id": tid,
                    "entry_price": float(avg_px),
                    "base_size": float(base_sz),
                    "buy_cost_usd": float(buy_cost),
                    "truth_valid": True,
                },
            )
        except SnapshotWriteError as exc:
            mark_trade_invalid(pos, reason=str(exc))
            logger.critical("NTE snapshot failure on entry fill; marking trade invalid trade_id=%s (%s)", tid, exc)

        try:
            self._reconcile_entry_fill(pid, buy_cost)
        except Exception as exc:
            reality_halt(str(exc))
            raise
        try:
            bump_exec_counter("limit_entries_filled")
        except Exception:
            pass
        logger.info("NTE limit filled → position %s size=%.6f", pid, base_sz)

    def _fills_to_stats(self, fills: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        try:
            return aggregate_fills_to_stats([f for f in fills if isinstance(f, dict)])
        except FillMismatchAbort:
            reality_halt("FILL_MISMATCH_ABORT")
            raise

    def _reconcile_entry_fill(self, product_id: str, buy_cost_usd: float) -> None:
        """Post-entry: venue base balance vs internal sum; capital velocity trade count."""
        bcc = base_currency_for_product(product_id)
        internal_sum = sum(
            float(p.get("base_size") or 0)
            for p in open_positions_list(self.state)
            if base_currency_for_product(str(p.get("product_id") or "")) == bcc
        )
        reconcile_coinbase_spot_base(self._client, bcc, internal_sum)
        try:
            record_trade_executed(notional_usd=float(buy_cost_usd), net_pnl_usd=None)
        except Exception as exc:
            logger.debug("record_trade_executed entry: %s", exc)

    def _entry_sort_key(self, product_id: str) -> Tuple[int, float]:
        """Lower tuple sorts first — execution queue (validated + latency before candidate)."""
        wb, wa = self._ws_bid_ask(product_id)
        try:
            feat = compute_features(
                store=self.store,
                client=self._client,
                product_id=product_id,
                spike_block_pct=self.settings.spike_block_pct,
                min_quote_volume_24h=self.settings.min_quote_volume_24h,
                bid_override=wb,
                ask_override=wa,
            )
        except Exception:
            return (99, 0.0)
        if feat is None:
            return (99, 0.0)
        sv = self._short_vol_bps(feat, product_id)
        dec = pick_live_route(feat, self.store, self.launch, short_vol_bps=sv)
        if dec is None or dec.chosen is None:
            return (99, 0.0)
        sig = dec.chosen
        lat_snap = build_market_snapshot_for_latency(
            product_id=product_id,
            venue="coinbase",
            mid=float(feat.mid),
            spread_pct=float(feat.spread_pct),
            store=self.store,
            quote_volume_24h=float(feat.quote_volume_24h or 0.0),
        )
        lat_sigs = detect_latency_signal(lat_snap)
        edge_asg = resolve_coinbase_edge(str(sig.name or "n/a"), product_id, latency_signals=lat_sigs)
        strength = max((s.strength for s in lat_sigs), default=0.0)
        return sort_key_for_nt_product(
            execution_rank=edge_asg.execution_rank,
            latency_strength=strength,
        )

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
        try:
            self.state["nte_last_regime"] = str(getattr(feat, "regime", None) or "neutral")
            sp = float(getattr(feat, "spread_pct", None) or 0.0)
            self.state["nte_last_chop_score"] = min(1.0, max(0.0, sp * 8.0))
        except Exception:
            pass
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

        lat_snap = build_market_snapshot_for_latency(
            product_id=product_id,
            venue="coinbase",
            mid=float(feat.mid),
            spread_pct=float(feat.spread_pct),
            store=self.store,
            quote_volume_24h=float(feat.quote_volume_24h or 0.0),
        )
        lat_signals = detect_latency_signal(lat_snap)
        edge_asg = resolve_coinbase_edge(str(sig.name or "n/a"), product_id, latency_signals=lat_signals)
        if edge_asg.size_scale <= 0:
            logger.info("NTE skip entry: edge policy blocks scale (%s)", edge_asg.detail)
            return

        reg0 = str(getattr(feat, "regime", None) or "")
        if edge_asg.edge_id:
            er = EdgeRegistry().get(edge_asg.edge_id)
            tags = None
            if er is not None:
                if isinstance(er.required_conditions, dict):
                    tags = er.required_conditions.get("regime_tags")
                if tags is None:
                    tags = getattr(er, "regime_tags", None)
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
            "latency_signal_types": [x.signal_type for x in lat_signals],
            "latency_signal_strength": max((x.strength for x in lat_signals), default=0.0),
        }
        latency_fast_exit = bool(lat_signals) and float(edge_asg.latency_boost_multiplier) > 1.0 + 1e-9

        # ── Edge governance layer (production vs experimental vs blocked) ─────
        # Gate A must not trade without a validated edge + profit enforcement.
        try:
            from trading_ai.nte.execution.edge_governance import (
                decide_lane_and_strategy,
                detect_gate_a_edges,
            )
            from trading_ai.nte.execution.net_edge_gate import estimate_round_trip_cost_bps
            from trading_ai.nte.execution.profit_enforcement import (
                ProfitEnforcementConfig,
                evaluate_profit_enforcement,
                profit_enforcement_allows_or_reason,
            )
            from trading_ai.nte.config.coinbase_avenue1_launch import load_coinbase_avenue1_launch

            # closes from market memory (already maintained by compute_features)
            mm = self.store.load_json("market_memory.json") or {}
            closes = []
            if isinstance(mm, dict):
                cl = (mm.get("closes") or {}).get(product_id) if isinstance(mm.get("closes"), dict) else None
                if isinstance(cl, list):
                    closes = [float(x) for x in cl if isinstance(x, (int, float))][-120:]

            edges = detect_gate_a_edges(
                closes=closes,
                feat={
                    "mid": float(feat.mid),
                    "spread_pct": float(feat.spread_pct),
                    "z_score": float(feat.z_score),
                    "regime": str(feat.regime),
                    "quote_volume_24h": float(feat.quote_volume_24h or 0.0),
                },
            )
            spread_bps = float(feat.spread_pct) * 10_000.0
            launch = load_coinbase_avenue1_launch()
            # NTE entry is maker-intent (limit), exit is taker; enforce via maker+taker estimate.
            fee_bps = estimate_round_trip_cost_bps(
                spread_bps=0.0,
                maker_fee_pct=float(launch.fees.estimated_maker_fee_pct),
                taker_fee_pct=float(launch.fees.estimated_taker_fee_pct),
                assume_maker_entry=True,
            )
            slippage_bps = float(os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 10.0)

            # Experimental candidate strategy id: only EXP_* strategies may use experimental lane.
            cand_sid = str(sig.name or "").strip()
            if not cand_sid.startswith("EXP_"):
                cand_sid = ""

            gov = decide_lane_and_strategy(
                runtime_root=Path(os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip() or Path.home() / "ezras-runtime",
                gate_id="gate_a",
                candidate_product=product_id,
                candidate_strategy_id=cand_sid,
                edges=edges,
                estimated_fees_bps=float(fee_bps),
                estimated_slippage_bps=float(slippage_bps),
                spread_bps=float(spread_bps),
            )
            market_snap["edge_governance"] = gov
            if str(gov.get("lane") or "") == "blocked":
                logger.info("NTE do-nothing: edge governance blocked (%s)", gov.get("block_reason_if_any"))
                return

            # Profit enforcement uses the edge-governed expected_move/risk; evaluate after sizing (needs usd).
            market_snap["edge_family"] = gov.get("edge_family")
            market_snap["edge_confidence"] = gov.get("confidence")
            market_snap["expected_move_bps_edge"] = gov.get("expected_move_bps")
            market_snap["net_expected_edge_bps_edge"] = gov.get("net_expected_edge_bps")
            market_snap["lane"] = gov.get("lane")
            market_snap["strategy_mode"] = gov.get("strategy_mode")
            market_snap["strategy_id"] = gov.get("edge_family") if gov.get("lane") == "production" else (gov.get("strategy_id") or "")
        except Exception as exc:
            logger.warning("NTE do-nothing: edge governance exception (%s)", type(exc).__name__)
            return

        # ── Liquidity + sizing fail-closed prerequisites ────────────────────
        # If missing edge/confidence/liquidity, size must be 0 => no trade.
        try:
            from trading_ai.shark.coinbase_spot.liquidity_gate import evaluate_liquidity_gate

            book_depth = getattr(feat, "book_depth_usd", None)
            liq_row = {
                "volume_24h_usd": float(getattr(feat, "quote_volume_24h", None) or 0.0),
                "spread_bps": float(getattr(feat, "spread_pct", None) or 0.0) * 10_000.0
                if getattr(feat, "spread_pct", None) is not None
                else None,
                "book_depth_usd": float(book_depth) if book_depth is not None else 0.0,
            }
            liq_dec = evaluate_liquidity_gate(liq_row)
        except Exception:
            liq_dec = {"passed": False, "liquidity_score": None, "reject_reasons": ["liquidity_gate_exception"]}
        market_snap["liquidity_gate"] = liq_dec
        if not bool(liq_dec.get("passed")):
            logger.info("NTE do-nothing: liquidity gate blocked (%s)", liq_dec.get("reject_reasons"))
            return

        try:
            from trading_ai.learning.authority_model import ai_reasoning_gate_for_nt_entry
            from trading_ai.learning.self_learning_engine import build_derived_execution_reasoning

            derived = build_derived_execution_reasoning(
                product_id=product_id,
                strategy_route=str(sig.name or "n/a"),
                regime=str(getattr(feat, "regime", None) or ""),
                spread_pct=float(getattr(feat, "spread_pct", None) or 0.0),
                edge_detail=str(getattr(edge_asg, "detail", None) or edge_asg),
            )
            ok_rs, rs_det = ai_reasoning_gate_for_nt_entry(
                product_id=product_id,
                derived_reasoning=derived,
            )
            if not ok_rs:
                logger.info("NTE skip entry: ai_reasoning_gate (%s)", rs_det)
                return
            try:
                self.state["nte_last_entry_reasoning"] = derived
            except Exception:
                pass
        except Exception as exc:
            logger.debug("ai execution reasoning gate: %s", exc)

        if self.launch.global_risk.shadow_compare_enabled:
            self._shadow_log(product_id, dec, feat)

        # ── Fail-closed sizing: require edge/confidence/liquidity score ─────
        eg = market_snap.get("edge_governance") if isinstance(market_snap.get("edge_governance"), dict) else {}
        edge_family = eg.get("edge_family") if isinstance(eg, dict) else None
        conf = eg.get("confidence") if isinstance(eg, dict) else None
        net_edge_bps = eg.get("net_expected_edge_bps") if isinstance(eg, dict) else None
        liq_score = (market_snap.get("liquidity_gate") or {}).get("liquidity_score") if isinstance(market_snap.get("liquidity_gate"), dict) else None
        if edge_family is None or conf is None or net_edge_bps is None or liq_score is None:
            logger.info("NTE do-nothing: sizing fail-closed (missing edge/confidence/liquidity)")
            return
        try:
            conf_f = float(conf)
            net_edge_f = float(net_edge_bps)
            liq_f = float(liq_score)
        except (TypeError, ValueError):
            logger.info("NTE do-nothing: sizing fail-closed (unparseable edge/confidence/liquidity)")
            return
        if conf_f <= 0.0 or net_edge_f <= 0.0 or liq_f <= 0.0:
            logger.info("NTE do-nothing: sizing fail-closed (non-positive edge/confidence/liquidity)")
            return

        usd = max(10.0, equity * float(dec.position_base_pct))
        usd = min(usd, equity * float(dec.position_max_pct), float(self._client.get_usd_balance()) * 0.98)
        usd = min(usd, equity * size_fraction * 1.25)
        usd *= float(edge_asg.size_scale)
        usd *= float(edge_asg.latency_boost_multiplier)
        gr = self.launch.global_risk
        if gr.launch_session_clamp:
            usd = min(usd, equity * float(gr.clamp_equity_per_trade_pct_max))
        cap_sz = CapitalEngine.get_trade_size(equity)
        usd = min(usd, cap_sz)
        usd *= float(self._strategy_validation.priority_multiplier(str(sig.name or "unknown")))
        usd *= float(getattr(self, "_adaptive_size_mult", 1.0))
        open_exp = sum(float(p.get("buy_cost_usd") or 0.0) for p in open_positions_list(self.state))
        open_gate = sum(
            float(p.get("buy_cost_usd") or 0.0)
            for p in open_positions_list(self.state)
            if str(p.get("trading_gate") or "").strip().lower() == "gate_a"
        )
        usd = _cap_notional(
            proposed_usd=float(usd),
            equity_usd=float(equity),
            open_avenue_exposure_usd=float(open_exp),
            open_gate_exposure_usd=float(open_gate),
        )
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

        # ── Snapshots: master + edge at entry (fail-closed) ─────────────────
        try:
            from trading_ai.runtime.trade_snapshots import (
                SnapshotWriteError,
                snapshot_trades_edge,
                snapshot_trades_master,
            )

            trade_id = f"nte_{product_id}_{int(time.time())}"
            master = {
                "trade_id": trade_id,
                "timestamp_open": datetime.now(timezone.utc).isoformat(),
                "avenue_id": "coinbase",
                "gate_id": "gate_a",
                "product_id": product_id,
                "strategy_id": str(market_snap.get("strategy_id") or str(sig.name or "")),
                "capital_allocated_usd": float(usd),
                "truth_valid": True,
            }
            snapshot_trades_master(trade_id=trade_id, avenue_id="coinbase", gate_id="gate_a", payload=master)
            snapshot_trades_edge(
                trade_id=trade_id,
                avenue_id="coinbase",
                gate_id="gate_a",
                edge_snapshot={
                    "edge_family": str(edge_family),
                    "confidence": float(conf_f),
                    "net_expected_edge_bps": float(net_edge_f),
                    "liquidity_score": float(liq_f),
                    "liquidity_gate": market_snap.get("liquidity_gate"),
                    "edge_governance": eg,
                },
            )
        except SnapshotWriteError as exc:
            logger.critical("NTE do-nothing: snapshot failure; trade invalid (%s)", exc)
            return

        if (os.environ.get("EZRAS_TRADING_INTELLIGENCE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            try:
                from trading_ai.intelligence.first_20 import adjust_for_first_20
                from trading_ai.intelligence.market_filter import passes_market_conditions
                from trading_ai.intelligence.performance_guard import adjust_for_loss_streak
                from trading_ai.nte.databank.local_trade_store import load_all_trade_events
                from trading_ai.organism.deployment_metrics import load_deployment_metrics

                total_tr = int(load_deployment_metrics().get("total_trades") or 0)
                m0, _ = adjust_for_first_20(total_tr)
                usd *= m0
                ev = load_all_trade_events()
                tail = [
                    {"net_pnl": float(x.get("net_pnl_usd") or x.get("net_pnl") or 0)}
                    for x in ev[-40:]
                    if isinstance(x, dict)
                ]
                m1, h = adjust_for_loss_streak(tail)
                if h == "halt":
                    logger.info("NTE skip entry: intelligence loss streak halt")
                    return
                usd *= m1
                liq = max(float(feat.quote_volume_24h or 0.0), usd * 100.0)
                ok_m, mr = passes_market_conditions(feat.bid, feat.ask, feat.mid, liq, usd)
                if not ok_m:
                    logger.info("NTE skip entry: market intelligence (%s)", mr)
                    return
            except Exception as exc:
                logger.debug("intelligence sizing/market skipped: %s", exc)

        if usd < 10.0:
            return

        # ── Profit enforcement (hard) using edge-governed expected move/risk ──
        try:
            eg = market_snap.get("edge_governance") or {}
            if isinstance(eg, dict) and str(eg.get("lane") or "") in ("production", "experimental"):
                cfg = ProfitEnforcementConfig(
                    min_expected_net_edge_bps=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_EDGE_BPS") or 2.0),
                    min_expected_net_pnl_usd=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_PNL_USD") or 0.05),
                    min_reward_to_risk=float(os.environ.get("EZRAS_MIN_REWARD_TO_RISK") or 1.10),
                    slippage_buffer_bps=float(os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 10.0),
                )
                # fee + spread already accounted in edge governance; provide fee/spread explicitly here.
                fee_bps_rt = float(eg.get("estimated_fees_bps") or 0.0)
                spread_bps_rt = float(eg.get("spread_bps") or 0.0)
                pe = evaluate_profit_enforcement(
                    runtime_root=Path(os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip() or Path.home() / "ezras-runtime",
                    trade_id=f"nte_pre_{product_id}",
                    avenue_id="A",
                    gate_id="gate_a",
                    product_id=product_id,
                    quote_usd=float(usd),
                    spread_bps=float(spread_bps_rt),
                    fee_bps_round_trip=float(fee_bps_rt),
                    expected_gross_move_bps=float(eg.get("expected_move_bps") or 0.0),
                    expected_risk_bps=max(1e-9, float(eg.get("expected_move_bps") or 0.0) / max(1.05, float(cfg.min_reward_to_risk))),
                    config=cfg,
                    extra={"surface": "nte_coinbase_engine", "lane": eg.get("lane"), "edge_family": eg.get("edge_family")},
                    write_artifact=True,
                )
                market_snap["profit_enforcement"] = pe
                ok_pe, why_pe = profit_enforcement_allows_or_reason(pe)
                if not ok_pe:
                    logger.info("NTE do-nothing: profit enforcement blocked (%s)", why_pe)
                    return
        except Exception as exc:
            logger.warning("NTE do-nothing: profit enforcement exception (%s)", type(exc).__name__)
            return

        vs = get_venue_state("coinbase")
        if not vs.allow_trading():
            logger.info("NTE skip entry: venue shutdown (coinbase)")
            return

        ok_mv, r_mv = check_market_reality_pre_trade(
            bid=feat.bid,
            ask=feat.ask,
            quote_volume_24h=feat.quote_volume_24h,
            net_edge_bps=float(getattr(dec, "net_edge_bps", 0.0) or 0.0),
            spread_bps_est=float(feat.spread_pct) * 10000.0,
        )
        if not ok_mv:
            logger.info("NTE skip entry: market reality (%s)", r_mv)
            return

        ok_fee, r_fee = fee_dominance_from_dec(usd, dec, float(feat.spread_pct) * 10000.0)
        if not ok_fee:
            logger.info("NTE skip entry: fee dominance (%s)", r_fee)
            return

        ok_cv, r_cv = capital_velocity_allows_trade(
            venue="coinbase",
            proposed_notional_usd=usd,
            account_equity_usd=equity,
        )
        if not ok_cv:
            logger.info("NTE skip entry: capital velocity (%s)", r_cv)
            return

        try:
            from trading_ai.control.system_execution_lock import (
                require_live_execution_allowed,
                validate_nt_entry_hard_guard,
            )

            ok_lock, lock_reason = require_live_execution_allowed("gate_a")
            if not ok_lock:
                logger.info("NTE skip entry: system execution lock (%s)", lock_reason)
                return
            hg = validate_nt_entry_hard_guard(
                self._client,
                product_id=product_id,
                quote_notional_usd=float(usd),
            )
            if not hg.ok:
                logger.info("NTE skip entry: hard execution guard (%s)", hg.reason)
                return
        except Exception as exc:
            logger.warning("NTE skip entry: execution lock / hard guard error (%s)", exc)
            return

        mid = feat.mid
        off = float(dec.strategy_params.get("entry_offset_bps", self.settings.entry_limit_offset_bps)) / 10000.0
        limit_px = mid * (1.0 - off)
        if limit_px >= feat.ask:
            limit_px = feat.bid * (1.0 - off * 0.5) if feat.bid > 0 else mid * (1.0 - off)
        base_sz = usd / max(limit_px, 1e-12)
        base_str = _fmt_base(base_sz, product_id)
        lim_str = _fmt_limit_price(limit_px, product_id)

        if deployment_enforcement_enabled():
            assert_system_not_halted()
            ref_px = max(float(limit_px), float(feat.mid or 0.0), 1e-12)
            DeploymentGuard().validate_pre_trade({"quote_size": float(usd), "price": ref_px})

        try:
            from trading_ai.control.kill_switch import kill_switch_active
            from trading_ai.risk.daily_loss_guard import check_daily_loss_limit

            if kill_switch_active():
                logger.warning("NTE skip entry: operator kill switch")
                return
            dl_b, _ = check_daily_loss_limit()
            if dl_b:
                logger.warning("NTE skip entry: daily loss limit")
                return
        except Exception:
            pass

        if _paper_mode():
            logger.info(
                "NTE PAPER limit BUY %s size=%s @ %s (strategy=%s)",
                product_id,
                base_str,
                lim_str,
                sig.name,
            )
            return

        # ── Universal gap candidate (required for any live BUY order) ─────────
        auth_tok = None
        prob_tok = None
        try:
            eg = (market_snap.get("edge_governance") or {}) if isinstance(market_snap, dict) else {}
            edge_family = str(eg.get("edge_family") or market_snap.get("edge_family") or "").strip()
            conf = eg.get("confidence", market_snap.get("edge_confidence"))
            try:
                conf_f = float(conf)
            except (TypeError, ValueError):
                conf_f = None  # fail-closed

            gap_type = map_coinbase_gap_type(edge_family, latency_trade=bool(lat_signals))
            if gap_type is None:
                # Fail-closed: unknown gap family is not permitted to reach live order methods.
                logger.info("NTE skip entry: gap_type_unmappable edge_family=%s", edge_family)
                return

            # Fair value proxy: expected move bps from edge governance applied to mid.
            # This is not 'fake': if expected_move_bps missing/0, candidate becomes invalid.
            try:
                mid_f = float(feat.mid)
            except (TypeError, ValueError):
                mid_f = 0.0
            try:
                expected_move_bps = float(
                    eg.get("expected_move_bps") or market_snap.get("expected_move_bps_edge") or 0.0
                )
            except (TypeError, ValueError):
                expected_move_bps = 0.0
            if expected_move_bps == 0.0:
                logger.info("NTE skip entry: missing_expected_move_bps_for_candidate")
                return
            est_true = mid_f * (1.0 + (expected_move_bps / 10000.0)) if mid_f > 0 and expected_move_bps != 0 else 0.0
            # Edge% vs market price.
            edge_pct = ((est_true / mid_f) - 1.0) * 100.0 if mid_f > 0 and est_true > 0 else 0.0

            liq = coinbase_liquidity_score(
                quote_volume_24h_usd=float(getattr(feat, "quote_volume_24h", 0.0) or 0.0),
                proposed_notional_usd=float(usd),
            )

            # Cost estimates: from edge governance if present; else fail-closed.
            try:
                fees_bps_rt = float(eg.get("estimated_fees_bps") or 0.0)
            except (TypeError, ValueError):
                fees_bps_rt = 0.0
            try:
                slip_bps_rt = float(os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 0.0)
            except (TypeError, ValueError):
                slip_bps_rt = 0.0
            fees_est = (float(usd) * (fees_bps_rt / 10000.0)) if fees_bps_rt > 0 else 0.0
            slip_est = (float(usd) * (slip_bps_rt / 10000.0)) if slip_bps_rt > 0 else 0.0

            emode = map_coinbase_execution_mode(maker_intent=True, may_fallback_market=False)

            if conf_f is None:
                logger.info("NTE skip entry: missing_confidence_score_from_edge_governance")
                return

            edge_score = float(edge_pct) * float(conf_f)
            cand_id = new_universal_candidate_id(prefix="a_ugc")
            cand0 = UniversalGapCandidate(
                candidate_id=str(cand_id),
                edge_percent=float(edge_pct),
                edge_score=float(edge_score),
                confidence_score=float(conf_f),
                execution_mode=str(emode),
                gap_type=str(gap_type),
                estimated_true_value=float(est_true),
                liquidity_score=float(liq),
                fees_estimate=float(fees_est),
                slippage_estimate=float(slip_est),
                must_trade=False,
            )
            gdec = evaluate_candidate(cand0)
            if not gdec.should_trade:
                logger.info(
                    "NTE skip entry: universal_gap_engine (%s)",
                    ",".join(gdec.rejection_reasons or ["rejected"]),
                )
                return
            cand = UniversalGapCandidate(**{**cand0.to_dict(), "must_trade": True})
            # Universal sizing (fail-closed) based on candidate.
            sdec = size_from_candidate(
                candidate=cand,
                equity_usd=float(equity),
                gate_id="A_CORE",
                avenue_id="A",
            )
            if not sdec.approved:
                logger.info("NTE skip entry: sizing_rejected (%s)", sdec.cap_reason)
                return
            usd = min(float(usd), float(sdec.recommended_notional))
            tok = candidate_context_set(cand)
            # Mission probability tiers must be enforced at the authoritative live order guard.
            # Use edge-governance confidence as the execution-time probability proxy (0..1).
            try:
                from trading_ai.shark.mission import mission_probability_set

                prob_tok = mission_probability_set(float(conf_f))
            except Exception:
                prob_tok = None
            # Single authoritative Avenue A live BUY path marker (enforced at venue order guard).
            auth_tok = authoritative_live_buy_path_set("nte_only")
        except Exception as exc:
            logger.warning("NTE skip entry: candidate_build_failed (%s)", type(exc).__name__)
            return

        if _use_limit_entries():
            try:
                res = self._place_limit_buy(product_id, base_str, lim_str)
            finally:
                try:
                    candidate_context_reset(tok)  # type: ignore[name-defined]
                except Exception:
                    pass
                if auth_tok is not None:
                    try:
                        authoritative_live_buy_path_reset(auth_tok)  # type: ignore[name-defined]
                    except Exception:
                        pass
                if prob_tok is not None:
                    try:
                        from trading_ai.shark.mission import mission_probability_reset

                        mission_probability_reset(prob_tok)
                    except Exception:
                        pass
            if not getattr(res, "success", False):
                logger.warning("NTE limit buy failed %s", getattr(res, "reason", res))
                return
            oid = str(getattr(res, "order_id", "") or "")
            ck = new_position_id()

            # Canonical snapshots (entry intent + candidate) — required evidence surfaces.
            try:
                runtime_root = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
                rr: Optional[Path] = runtime_root if str(runtime_root) else None
            except Exception:
                rr = None
            try:
                cand0 = cand
                mres = write_master_snapshot(
                    rr,
                    {
                        "trade_id": ck,
                        "venue_id": "coinbase",
                        "gate_id": "A_CORE",
                        "trade_type": "core",
                        "symbol_or_contract": product_id,
                        "gap_type": cand0.gap_type,
                        "entry_timestamp": None,
                        "exit_timestamp": None,
                        "side": "BUY",
                        "quantity": float(base_sz),
                        "status": "PENDING_ENTRY",
                        "exit_reason": None,
                        "live_or_paper": "live",
                        "authoritative_live_buy_path": "nte_only",
                        "universal_gap_candidate": cand0.to_dict(),
                    },
                )
                eres = write_edge_snapshot(
                    rr,
                    {
                        "trade_id": ck,
                        "candidate_id": str(cand0.candidate_id),
                        "market_price_at_entry": float(feat.mid),
                        "estimated_true_value": float(cand0.estimated_true_value),
                        "edge_percent": float(cand0.edge_percent),
                        "edge_score": float(cand0.edge_score),
                        "confidence_score": float(cand0.confidence_score),
                        "liquidity_score": float(cand0.liquidity_score),
                        "fees_estimate": float(cand0.fees_estimate),
                        "slippage_estimate": float(cand0.slippage_estimate),
                        "gap_type": str(cand0.gap_type),
                    },
                )
                xres = write_execution_snapshot(
                    rr,
                    {
                        "trade_id": ck,
                        "order_type_entry": "limit_gtc_post_only",
                        "order_type_exit": None,
                        "intended_execution_mode": cand0.execution_mode,
                        "actual_execution_mode": None,
                        "posted_limit_price": float(limit_px),
                        "actual_fill_price": None,
                        "fill_delay": None,
                        "partial_fill_flag": None,
                        "maker_or_taker_entry": "maker",
                        "maker_or_taker_exit": None,
                        "execution_grade": None,
                        "slippage_actual": None,
                        "fees_actual": None,
                        "entry_order_id": oid,
                        "authoritative_live_buy_path": "nte_only",
                    },
                )
                if not (mres.ok and eres.ok and xres.ok):
                    try:
                        self._client.cancel_order(oid)
                    except Exception:
                        pass
                    logger.critical(
                        "FAIL_CLOSED missing_snapshot_write entry trade_id=%s master_ok=%s edge_ok=%s exec_ok=%s",
                        ck,
                        bool(mres.ok),
                        bool(eres.ok),
                        bool(xres.ok),
                    )
                    return
            except Exception as exc:
                try:
                    self._client.cancel_order(oid)
                except Exception:
                    pass
                logger.critical("FAIL_CLOSED snapshot_write_exception entry trade_id=%s err=%s", ck, type(exc).__name__)
                return
            self.state.setdefault("pending_entry_orders", []).append(
                {
                    "trade_id": trade_id,
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
                    "latency_fast_exit": latency_fast_exit,
                    "latency_trade": bool(lat_signals),
                }
            )
            try:
                bump_exec_counter("limit_entries_placed")
            except Exception:
                pass
            logger.info("NTE limit BUY placed %s @ %s oid=%s", product_id, lim_str, oid[:16])
            return

        # Strict execution: no market-entry fallback.
        if _strict_execution_mode():
            logger.critical("STRICT_EXECUTION_MODE: refusing market-entry fallback for %s", product_id)
            try:
                candidate_context_reset(tok)  # type: ignore[name-defined]
            except Exception:
                pass
            return

        try:
            res = self._client.place_market_buy(product_id, usd)
        finally:
            try:
                candidate_context_reset(tok)  # type: ignore[name-defined]
            except Exception:
                pass
            if prob_tok is not None:
                try:
                    from trading_ai.shark.mission import mission_probability_reset

                    mission_probability_reset(prob_tok)
                except Exception:
                    pass
        if not getattr(res, "success", False):
            logger.warning("NTE market buy failed %s: %s", product_id, getattr(res, "reason", res))
            return
        roid = str(getattr(res, "order_id", "") or "")
        try:
            fills_mb = wait_for_fill(
                self._client,
                roid,
                max_wait_sec=_REALITY_ORDER_WAIT_SEC,
                max_stale_order_age_sec=_REALITY_ORDER_TS_MAX_AGE,
            )
        except Exception as exc:
            reality_halt(str(exc))
            raise
        bsz, apx, bfee = self._fills_to_stats(fills_mb)
        if bsz <= 0 or apx <= 0:
            logger.warning("NTE could not resolve market buy fill for %s", product_id)
            return
        if deployment_enforcement_enabled():
            DeploymentGuard().validate_post_buy(
                FillSnapshot(base_size=bsz, quote_size=bsz * apx, price=apx)
            )
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
            latency_fast_exit=latency_fast_exit,
            latency_trade=bool(lat_signals),
        )

    def _place_limit_buy(self, product_id: str, base_str: str, lim_str: str) -> Any:
        po = bool(self.settings.post_only_limits)
        res = self._client.place_limit_gtc(product_id, "BUY", base_str, lim_str, post_only=po)
        if getattr(res, "success", False):
            return res
        if po and not _strict_execution_mode():
            logger.info("NTE retry limit BUY without post_only (STRICT_EXECUTION_MODE=false)")
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
        latency_fast_exit: bool = False,
        latency_trade: bool = False,
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
        if latency_fast_exit:
            ts_sec = int(min(float(ts_sec), float(LATENCY_MAX_HOLD_SECONDS)))
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
            "trading_gate": "gate_a",
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
            "latency_trade": latency_trade,
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
        try:
            self._reconcile_entry_fill(product_id, buy_cost)
        except Exception as exc:
            reality_halt(str(exc))
            raise
        if execution == "market_ioc":
            try:
                bump_exec_counter("market_entries")
            except Exception:
                pass
        logger.info("NTE opened %s %s exec=%s size=%.6f", sig.name, product_id, execution, base_sz)
