"""
Live monitoring dashboard (A→K) — behavior quality, not PnL alone.

Call :func:`build_live_monitoring_dashboard` every 5–10 minutes in live mode, or wire
from a scheduler. Pass :class:`CoinbaseNTEngine` when available for real WS ages.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.capital_ledger import load_ledger, net_equity_estimate
from trading_ai.nte.execution.state import load_state
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.nte.monitoring.execution_counters import load_counters
from trading_ai.nte.monitoring.hard_stops import evaluate_hard_stops
from trading_ai.nte.paths import nte_memory_dir, nte_system_health_path

logger = logging.getLogger(__name__)


def strategy_ab_label(setup_type: Optional[str]) -> str:
    s = (setup_type or "").lower()
    if "mean_reversion" in s:
        return "A"
    if "continuation" in s or "pullback" in s:
        return "B"
    return "—"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.debug("dashboard read %s: %s", path, exc)
        return None


def _shadow_tail(n: int = 12) -> List[Dict[str, Any]]:
    p = nte_memory_dir() / "shadow_compare_events.json"
    d = _read_json(p) or {}
    ev = d.get("events") or []
    if not isinstance(ev, list):
        return []
    return [x for x in ev if isinstance(x, dict)][-n:]


def build_live_monitoring_dashboard(
    *,
    engine: Any = None,
    user_ws_stale: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Build a single JSON-serializable snapshot: sections A–K + hard_stop + ceo_prompts.

    ``engine`` — optional :class:`CoinbaseNTEngine` with ``_ws_feed`` for market WS age.
    ``user_ws_stale`` — set when user stream is wired (else None = unknown).
    """
    now = time.time()
    h = _read_json(nte_system_health_path()) or {}
    st = load_state()
    store = MemoryStore()
    store.ensure_defaults()
    tm = store.load_json("trade_memory.json")
    trades: List[Dict[str, Any]] = [t for t in (tm.get("trades") or []) if isinstance(t, dict)]
    cb_trades = [t for t in trades if str(t.get("avenue") or t.get("avenue_id") or "") == "coinbase"]

    # Rolling net for hard-stop helper (last 10 closed)
    recent = cb_trades[-10:]
    roll_net = sum(float(t.get("net_pnl_usd") or 0) for t in recent)

    stop, stop_reasons = evaluate_hard_stops(
        user_ws_stale=user_ws_stale,
        net_pnl_recent_usd=roll_net if len(recent) >= 5 else None,
    )

    # A — system health
    ws_age: Optional[float] = None
    ws_stale: Optional[bool] = None
    ws_thread_alive = None
    feed = getattr(engine, "_ws_feed", None) if engine is not None else None
    if feed is not None:
        try:
            ws_age = float(feed.last_tick_age_sec())
            ws_stale = bool(feed.is_stale(max_age_sec=90.0))
            th = getattr(feed, "_thread", None)
            ws_thread_alive = bool(th and th.is_alive())
        except Exception as exc:
            logger.debug("ws feed metrics: %s", exc)

    section_a = {
        "ws_market": {
            "live": feed is not None
            and ws_age is not None
            and ws_age < float("inf")
            and ws_stale is False,
            "stale": ws_stale,
            "unknown": feed is None,
            "last_tick_age_sec": ws_age,
            "thread_alive": ws_thread_alive,
        },
        "ws_user": {
            "live": user_ws_stale is False,
            "stale": user_ws_stale is True,
            "unknown": user_ws_stale is None,
            "note": "Wire CoinbaseUserStreamFeed + pass user_ws_stale for full signal.",
        },
        "degraded_mode": {
            "on": h.get("healthy") is False or bool(h.get("degraded_components")),
            "components": list(h.get("degraded_components") or []),
        },
        "polling_fallback": {
            "note": "If user WS stale, prefer REST fill reconciler (see ws_user_feed docstring).",
        },
        "system_health_ts": h.get("ts"),
        "timestamps": h.get("timestamps") or {},
    }

    counters = load_counters()
    placed = max(1, int(counters.get("limit_entries_placed") or 0))
    filled = int(counters.get("limit_entries_filled") or 0)
    stale_c = int(counters.get("stale_pending_canceled") or 0)
    cancel_rate = stale_c / float(placed) if placed else 0.0
    pending_fill_rate = filled / float(placed) if placed else 0.0

    # B–H aggregate from last trades + state
    last5 = cb_trades[-5:]
    section_b_order_flow = {
        "note": "Per-trade: prefer maker intent + fill quality fields on trade_memory rows.",
        "recent_trade_tail_fields": [
            {
                "entry_maker_intent": t.get("entry_maker_intent"),
                "entry_execution": t.get("entry_execution"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in last5
        ],
    }

    section_c_spread = {
        "recent_spread_bps": [float(t.get("spread_bps") or (float(t.get("spread") or 0) * 10000.0)) for t in last5],
        "recent_regime": [str(t.get("regime") or "") for t in last5],
    }

    section_d_router = {
        "shadow_events_tail": _shadow_tail(8),
    }

    section_e_edge = {
        "expected_vs_actual_tail": [
            {
                "expected_edge_bps": t.get("expected_edge_bps"),
                "net_pnl_usd": t.get("net_pnl_usd"),
                "realized_move_bps": t.get("realized_move_bps"),
            }
            for t in last5
        ],
    }

    wins = [t for t in cb_trades if float(t.get("net_pnl_usd") or 0) > 0]
    losses = [t for t in cb_trades if float(t.get("net_pnl_usd") or 0) <= 0]
    fees_sum = sum(float(t.get("fees") or t.get("fees_usd") or 0) for t in cb_trades)

    section_f_pnl = {
        "trade_count": len(cb_trades),
        "win_rate": (len(wins) / len(cb_trades)) if cb_trades else 0.0,
        "total_fees_usd": fees_sum,
        "total_net_usd": sum(float(t.get("net_pnl_usd") or 0) for t in cb_trades),
        "avg_win": (sum(float(t.get("net_pnl_usd") or 0) for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(float(t.get("net_pnl_usd") or 0) for t in losses) / len(losses)) if losses else 0.0,
    }

    section_g_positions = {
        "open_positions": len(st.get("positions") or []),
        "pending_limits": len(st.get("pending_entry_orders") or []),
        "consecutive_losses": int(st.get("consecutive_losses") or 0),
    }

    section_h_pending = {
        "pending_orders": len(st.get("pending_entry_orders") or []),
        "limit_placed": placed,
        "limit_filled": filled,
        "stale_canceled": stale_c,
        "pending_fill_ratio": round(pending_fill_rate, 4),
        "cancel_rate": round(cancel_rate, 4),
    }

    section_i_slippage = {
        "shadow_compare_tail": _shadow_tail(6),
        "trade_realized_move_bps_tail": [t.get("realized_move_bps") for t in last5],
    }

    led = load_ledger()
    eq = net_equity_estimate()
    section_j_risk = {
        "equity_estimate_usd": eq,
        "ledger_realized_pnl_usd": float(led.get("realized_pnl_usd") or 0),
        "deposits_usd": float(led.get("deposits_usd") or 0),
        "consecutive_losses": int(st.get("consecutive_losses") or 0),
        "paused_until": st.get("paused_until"),
    }

    section_k_ceo = {
        "questions": [
            "Which is stronger right now: route A (mean reversion) or B (continuation pullback)?",
            "Are we net profitable after fees over the last 10 closes?",
            "Are fills clean (maker intent, few stale cancels)?",
            "Is anything clearly broken (WS, degraded mode, risk pause)?",
        ],
        "interval_hours": "2–3",
    }

    hard_stop = {
        "stop_new_entries": stop,
        "reasons": stop_reasons,
        "rolling_net_last10_usd": roll_net,
    }

    out = {
        "schema_version": 1,
        "generated_at_ts": now,
        "A_system_health": section_a,
        "B_order_flow_quality": section_b_order_flow,
        "C_spread_and_regime": section_c_spread,
        "D_ab_router": section_d_router,
        "E_net_edge_vs_reality": section_e_edge,
        "F_pnl_quality": section_f_pnl,
        "G_position_behavior": section_g_positions,
        "H_pending_order_efficiency": section_h_pending,
        "I_slippage_and_drift": section_i_slippage,
        "J_risk_state": section_j_risk,
        "K_ceo_snapshot_prompts": section_k_ceo,
        "hard_stop": hard_stop,
    }
    return out


def write_live_dashboard_json(
    path: Optional[Path] = None,
    *,
    engine: Any = None,
    user_ws_stale: Optional[bool] = None,
) -> Path:
    """Persist dashboard next to NTE memory for external dashboards / tail -f."""
    p = path or (nte_memory_dir() / "live_monitoring_dashboard.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = build_live_monitoring_dashboard(engine=engine, user_ws_stale=user_ws_stale)
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return p
