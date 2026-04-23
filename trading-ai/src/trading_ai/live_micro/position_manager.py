from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.live_micro.first20 import update_first20_review
from trading_ai.live_micro.positions import (
    append_position_journal,
    load_open_positions,
    mark_position_closed,
    save_open_positions,
    upsert_position,
)
from trading_ai.live_micro.supabase_events import maybe_write_live_micro_event

logger = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _get_mid_price_public(product_id: str) -> Optional[float]:
    try:
        from trading_ai.shark.outlets.coinbase import _brokerage_public_request

        pid = (product_id or "").strip().upper()
        t = _brokerage_public_request(f"/market/products/{pid}/ticker")
        if not isinstance(t, dict):
            return None
        bid = _f(t.get("best_bid") or t.get("bid"))
        ask = _f(t.get("best_ask") or t.get("ask"))
        mid = _f(t.get("price"))
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
        return mid if mid > 0 else None
    except Exception:
        return None


def _parse_fills(fills: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (avg_price, base_qty) from Coinbase fills list when available.
    """
    total_qty = 0.0
    total_quote = 0.0
    for f in fills or []:
        if not isinstance(f, dict):
            continue
        price = _f(f.get("price") or f.get("fill_price") or f.get("trade_price"))
        size = _f(f.get("size") or f.get("filled_size") or f.get("base_size"))
        if price > 0 and size > 0:
            total_qty += size
            total_quote += price * size
    if total_qty <= 0:
        return None, None
    return (total_quote / total_qty), total_qty


def _exit_thresholds(entry_price: Optional[float]) -> Dict[str, Any]:
    ep = _f(entry_price) if entry_price is not None else 0.0
    tp_pct = _f(os.environ.get("EZRA_LIVE_MICRO_TAKE_PROFIT_PCT") or 0.006)  # 0.6% default
    sl_pct = _f(os.environ.get("EZRA_LIVE_MICRO_STOP_LOSS_PCT") or 0.004)  # 0.4% default
    tp_pct = max(0.0, min(0.05, tp_pct))
    sl_pct = max(0.0, min(0.05, sl_pct))
    return {
        "take_profit_price": (ep * (1.0 + tp_pct)) if ep > 0 else None,
        "stop_loss_price": (ep * (1.0 - sl_pct)) if ep > 0 else None,
        "take_profit_pct": tp_pct,
        "stop_loss_pct": sl_pct,
    }


def run_live_micro_position_manager_once(*, runtime_root: Path) -> Dict[str, Any]:
    """
    OPS loop:
    - monitors open positions
    - submits exits when TP/SL/max-hold triggers
    - probes exit fills, closes positions, updates first-20 cohort
    """
    root = Path(runtime_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    if not _truthy_env("EZRA_LIVE_MICRO_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "micro_disabled"}
    if not _truthy_env("COINBASE_EXECUTION_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "coinbase_execution_disabled"}

    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    client = CoinbaseClient()
    positions = load_open_positions(root)
    now = time.time()
    touched = 0
    exits_submitted = 0
    closed = 0

    for p in list(positions):
        if not isinstance(p, dict):
            continue
        status = str(p.get("status") or "").lower()
        if status not in ("open", "closing"):
            continue
        pos_id = str(p.get("position_id") or "").strip()
        pid = str(p.get("product_id") or "").strip().upper()
        if not pos_id or not pid:
            continue

        touched += 1
        entry_ts = _f(p.get("entry_ts") or p.get("entry_time") or 0.0)
        entry_price = p.get("entry_price")
        max_hold = int(_f(p.get("max_hold_sec") or os.environ.get("EZRA_LIVE_MICRO_MAX_HOLD_SEC") or 1800))

        mid = _get_mid_price_public(pid)
        th = _exit_thresholds(_f(entry_price) if entry_price is not None else None)

        append_position_journal(
            root,
            {
                "ts": now,
                "event": "position_monitored",
                "position_id": pos_id,
                "product_id": pid,
                "status": status,
                "mid": mid,
                "thresholds": th,
            },
        )
        maybe_write_live_micro_event(
            runtime_root=root,
            event="position_monitored",
            product_id=pid,
            position_id=pos_id,
            payload={"mid": mid, "status": status, "thresholds": th},
            dedupe_key=f"lm:position_monitored:{pos_id}:{int(now//30)}",
        )

        # If we're already closing, probe exit fills.
        if status == "closing":
            exit_order_id = str(p.get("exit_order_id") or "").strip()
            if not exit_order_id:
                # invalid state -> close defensively
                mark_position_closed(root, pos_id, {"exit_reason": "invalid_state_missing_exit_order_id", "realized_pnl_usd": None})
                append_position_journal(root, {"ts": now, "event": "position_closed", "position_id": pos_id, "product_id": pid, "exit_reason": "invalid_state_missing_exit_order_id"})
                closed += 1
                continue
            try:
                fills = client.get_fills(exit_order_id)
            except Exception:
                fills = []
            avg_exit, _qty = _parse_fills(list(fills or []))
            append_position_journal(
                root,
                {"ts": now, "event": "exit_fill_probe", "position_id": pos_id, "product_id": pid, "exit_order_id": exit_order_id, "fills_n": len(list(fills or []))},
            )
            maybe_write_live_micro_event(
                runtime_root=root,
                event="exit_fill_probe",
                product_id=pid,
                order_id=exit_order_id,
                position_id=pos_id,
                payload={"fills_n": len(list(fills or [])), "avg_exit_price": avg_exit},
                dedupe_key=f"lm:exit_fill_probe:{exit_order_id}:{int(now//30)}",
            )
            if not fills:
                continue

            quote_spent = _f(p.get("quote_spent") or 0.0)
            base_qty = _f(p.get("base_qty") or 0.0)
            realized = None
            if avg_exit is not None and base_qty > 0 and quote_spent > 0:
                realized = (float(avg_exit) * float(base_qty)) - float(quote_spent)
            patch = {
                "status": "closed",
                "exit_ts": now,
                "exit_price": avg_exit,
                "realized_pnl_usd": realized,
                "exit_reason": str(p.get("exit_reason") or "exit_filled"),
            }
            mark_position_closed(root, pos_id, patch)
            append_position_journal(root, {"ts": now, "event": "position_closed", "position_id": pos_id, "product_id": pid, **patch})
            maybe_write_live_micro_event(
                runtime_root=root,
                event="position_closed",
                product_id=pid,
                order_id=exit_order_id,
                position_id=pos_id,
                payload=patch,
                dedupe_key=f"lm:position_closed:{pos_id}",
            )
            # First-20 cohort update (advisory, durable)
            try:
                update_first20_review(
                    runtime_root=root,
                    closed_trade_summary={
                        "trade_id": exit_order_id,
                        "position_id": pos_id,
                        "product_id": pid,
                        "entry_price": p.get("entry_price"),
                        "exit_price": avg_exit,
                        "quote_spent": quote_spent,
                        "base_qty": base_qty,
                        "realized_pnl_usd": realized,
                        "hold_seconds": (now - entry_ts) if entry_ts > 0 else None,
                        "exit_reason": patch.get("exit_reason"),
                    },
                )
            except Exception:
                pass
            closed += 1
            continue

        # status == open: decide whether to exit
        exit_reason: Optional[str] = None
        tp = th.get("take_profit_price")
        sl = th.get("stop_loss_price")
        if mid is not None and tp is not None and float(mid) >= float(tp):
            exit_reason = "take_profit"
        elif mid is not None and sl is not None and float(mid) <= float(sl):
            exit_reason = "stop_loss"
        elif entry_ts > 0 and max_hold > 0 and (now - entry_ts) >= float(max_hold):
            exit_reason = "max_hold_time"

        if not exit_reason:
            continue

        base_qty = str(p.get("base_qty") or "").strip()
        if not base_qty:
            # cannot exit without base qty; mark failed honestly
            patch = {"status": "failed", "exit_reason": "missing_base_qty_for_exit"}
            upsert_position(root, {**p, **patch})
            append_position_journal(root, {"ts": now, "event": "exit_decision", "position_id": pos_id, "product_id": pid, "decision": "fail", "reason": patch["exit_reason"]})
            continue

        append_position_journal(root, {"ts": now, "event": "exit_decision", "position_id": pos_id, "product_id": pid, "decision": "exit", "reason": exit_reason, "mid": mid})
        maybe_write_live_micro_event(
            runtime_root=root,
            event="exit_decision",
            product_id=pid,
            position_id=pos_id,
            payload={"decision": "exit", "reason": exit_reason, "mid": mid},
            dedupe_key=f"lm:exit_decision:{pos_id}:{exit_reason}",
        )

        # Submit market sell
        res = client.place_market_sell(pid, base_qty, execution_gate="gate_b")
        exits_submitted += 1
        patch2 = {
            "status": "closing" if res.success else "failed",
            "exit_reason": exit_reason,
            "exit_order_id": (str(res.order_id or "").strip() if res.success else ""),
            "exit_submit_ts": now,
        }
        upsert_position(root, {**p, **patch2})
        append_position_journal(root, {"ts": now, "event": "exit_order_submitted", "position_id": pos_id, "product_id": pid, "success": bool(res.success), "exit_order_id": res.order_id, "reason": res.reason})
        maybe_write_live_micro_event(
            runtime_root=root,
            event="exit_order_submitted",
            product_id=pid,
            order_id=str(res.order_id or ""),
            position_id=pos_id,
            payload={"success": bool(res.success), "reason": res.reason},
            dedupe_key=f"lm:exit_order_submitted:{pos_id}:{exit_reason}",
        )

    # Persist updated open positions view (upserts wrote; ensure doc exists)
    try:
        save_open_positions(root, load_open_positions(root))
    except Exception:
        pass

    return {"ok": True, "touched": touched, "exits_submitted": exits_submitted, "closed": closed}

