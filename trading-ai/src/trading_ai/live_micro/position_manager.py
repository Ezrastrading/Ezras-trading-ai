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
    """Return (avg_price, base_qty) from Coinbase fills list when available."""
    try:
        from trading_ai.live_micro.fills import parse_coinbase_fills

        avg, base_qty, _q, _c, _diag = parse_coinbase_fills(list(fills or []))
        return avg, base_qty
    except Exception:
        return None, None


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


def _estimate_net_pnl_usd(*, mid: float, base_qty: float, quote_spent: float) -> Dict[str, Any]:
    """
    Conservative net PnL estimate for take-profit gating.
    Uses configurable fee + slippage buffer; does not require live fills.
    """
    try:
        fee_pct = float((os.environ.get("EZRA_LIVE_MICRO_EST_TOTAL_FEES_PCT") or "0.006").strip() or "0.006")
    except Exception:
        fee_pct = 0.006
    try:
        slip_bps = float((os.environ.get("EZRA_LIVE_MICRO_EXIT_SLIPPAGE_BPS") or "2.0").strip() or "2.0")
    except Exception:
        slip_bps = 2.0
    fee_pct = max(0.0, min(0.05, fee_pct))
    slip_pct = max(0.0, min(0.01, float(slip_bps) / 10000.0))
    gross = max(0.0, float(mid) * max(0.0, float(base_qty)))
    net_proceeds = gross * (1.0 - fee_pct - slip_pct)
    net = float(net_proceeds) - max(0.0, float(quote_spent))
    return {
        "gross_proceeds_est": gross,
        "net_proceeds_est": net_proceeds,
        "fees_pct_assumed": fee_pct,
        "slippage_bps_assumed": slip_bps,
        "net_pnl_est": net,
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
        if status not in ("pending_entry", "open", "closing", "failed"):
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

        # Pending entry: probe entry fills, promote to open, or cancel+release on timeout.
        if status == "pending_entry":
            entry_order_id = str(p.get("entry_order_id") or p.get("position_id") or "").strip()
            timeout = int(_f(p.get("pending_entry_timeout_sec") or os.environ.get("EZRA_LIVE_MICRO_ENTRY_FILL_TIMEOUT_SEC") or 120))
            timeout = max(30, min(600, timeout))
            entry_ts = _f(p.get("entry_ts") or 0.0)
            try:
                fills = client.get_fills(entry_order_id)
            except Exception:
                fills = []
            avg_entry, base_qty = _parse_fills(list(fills or []))
            append_position_journal(root, {"ts": now, "event": "entry_fill_probe", "position_id": pos_id, "product_id": pid, "entry_order_id": entry_order_id, "fills_n": len(list(fills or []))})
            maybe_write_live_micro_event(
                runtime_root=root,
                event="entry_fill_probe",
                product_id=pid,
                order_id=entry_order_id,
                position_id=pos_id,
                payload={"fills_n": len(list(fills or [])), "avg_entry_price": avg_entry},
                dedupe_key=f"lm:entry_fill_probe:{entry_order_id}:{int(now//30)}",
            )
            if fills and base_qty is not None:
                upsert_position(
                    root,
                    {
                        **p,
                        "status": "open",
                        "entry_price": avg_entry,
                        "base_qty": float(base_qty),
                    },
                )
                append_position_journal(root, {"ts": now, "event": "position_opened", "position_id": pos_id, "product_id": pid, "entry_price": avg_entry, "base_qty": base_qty})
                continue
            if entry_ts > 0 and (now - entry_ts) >= float(timeout):
                cancelled = False
                try:
                    cancelled = bool(client.cancel_order(entry_order_id))
                except Exception:
                    cancelled = False
                patch = {
                    "status": "closed",
                    "exit_reason": "entry_fill_timeout_cancelled" if cancelled else "entry_fill_timeout_cancel_failed",
                    "exit_ts": now,
                    "realized_pnl_usd": None,
                }
                mark_position_closed(root, pos_id, patch)
                append_position_journal(root, {"ts": now, "event": "position_closed", "position_id": pos_id, "product_id": pid, **patch})
                maybe_write_live_micro_event(
                    runtime_root=root,
                    event="position_closed",
                    product_id=pid,
                    order_id=entry_order_id,
                    position_id=pos_id,
                    payload=patch,
                    dedupe_key=f"lm:position_closed:{pos_id}",
                )
                closed += 1
            continue

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
                f20 = update_first20_review(
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
                # Telegram (non-spam): first close + cohort complete.
                try:
                    from trading_ai.automation.telegram_ops import send_telegram_with_idempotency

                    pnl = patch.get("realized_pnl_usd")
                    pnl_s = f"{float(pnl):.2f}" if pnl is not None else "n/a"
                    txt = f"✅ LIVE MICRO CLOSED {pid}\nPnL: ${pnl_s}\nReason: {patch.get('exit_reason')}"
                    send_telegram_with_idempotency(None, txt, dedupe_key=f"lm:tg:closed:{pos_id}", event_label="live_micro_position_closed")
                    if int(f20.get("completed_trades_count") or 0) == 1:
                        send_telegram_with_idempotency(None, "🟢 FIRST LIVE MICRO TRADE CLOSED (1/20). Review cohort artifact.", dedupe_key="lm:tg:first_close", event_label="live_micro_first_close")
                    if bool(f20.get("cohort_complete")):
                        send_telegram_with_idempotency(None, "🏁 LIVE MICRO FIRST-20 COHORT COMPLETE. Review live_micro_first20_review.json", dedupe_key="lm:tg:first20_complete", event_label="live_micro_first20_complete")
                except Exception:
                    pass
            except Exception:
                pass
            closed += 1
            continue

        # status == open/failed: repair base_qty if legacy bookkeeping stored quote-as-base
        try:
            from trading_ai.live_micro.fills import parse_coinbase_fills

            entry_order_id = str(p.get("entry_order_id") or pos_id or "").strip()
            ep0 = _f(p.get("entry_price") or 0.0)
            qs0 = _f(p.get("quote_spent") or 0.0)
            b0 = _f(p.get("base_qty") or 0.0)
            expected0 = (qs0 / ep0) if (qs0 > 0 and ep0 > 0) else None
            ratio0 = (b0 / expected0) if (expected0 is not None and expected0 > 0 and b0 > 0) else None
            needs_repair = bool(ratio0 is not None and (ratio0 < 0.2 or ratio0 > 5.0))
            if needs_repair and entry_order_id:
                try:
                    fills = client.get_fills(entry_order_id)
                except Exception:
                    fills = []
                avg_entry, base_qty, quote_from_fills, comm_quote, diag = parse_coinbase_fills(list(fills or []))
                if avg_entry is not None and base_qty is not None and avg_entry > 0 and base_qty > 0:
                    qs1 = float(quote_from_fills) if quote_from_fills is not None else float(qs0)
                    fees1 = float(comm_quote) if comm_quote is not None else 0.0
                    qs1 = float(qs1) + max(0.0, float(fees1))
                    expected1 = (qs1 / float(avg_entry)) if qs1 > 0 else None
                    ratio1 = (float(base_qty) / float(expected1)) if (expected1 is not None and expected1 > 0) else None
                    if ratio1 is None or (ratio1 >= 0.2 and ratio1 <= 5.0):
                        patch = {
                            "entry_price": float(avg_entry),
                            "base_qty": float(base_qty),
                            "quote_spent": float(qs1),
                            "fees_paid": float(fees1) if fees1 else p.get("fees_paid"),
                        }
                        upsert_position(root, {**p, **patch})
                        p = {**p, **patch}
                        append_position_journal(
                            root,
                            {
                                "ts": now,
                                "event": "position_base_qty_repaired",
                                "position_id": pos_id,
                                "product_id": pid,
                                "old_base_qty": float(b0),
                                "old_entry_price": float(ep0) if ep0 else None,
                                "old_quote_spent": float(qs0) if qs0 else None,
                                "new_base_qty": float(base_qty),
                                "new_entry_price": float(avg_entry),
                                "new_quote_spent": float(qs1),
                                "sanity_ratio_vs_quote_over_price_old": float(ratio0) if ratio0 is not None else None,
                                "sanity_ratio_vs_quote_over_price_new": float(ratio1) if ratio1 is not None else None,
                                "fills_diag": diag,
                            },
                        )
        except Exception:
            pass

        # status == open/failed: decide whether to exit
        exit_reason: Optional[str] = None
        tp = th.get("take_profit_price")
        sl = th.get("stop_loss_price")
        if mid is not None and tp is not None and float(mid) >= float(tp):
            # Net-PnL take profit gate (do not label/exit as TP when net would be negative).
            base_qty0 = _f(p.get("base_qty") or 0.0)
            quote_spent0 = _f(p.get("quote_spent") or 0.0)
            pnl = _estimate_net_pnl_usd(mid=float(mid), base_qty=float(base_qty0), quote_spent=float(quote_spent0))
            try:
                min_tp = float((os.environ.get("EZRA_LIVE_MICRO_MIN_NET_TAKE_PROFIT_USD") or "0.02").strip() or "0.02")
            except Exception:
                min_tp = 0.02
            append_position_journal(root, {"ts": now, "event": "exit_pnl_estimate", "position_id": pos_id, "product_id": pid, **pnl})
            maybe_write_live_micro_event(
                runtime_root=root,
                event="exit_pnl_estimate",
                product_id=pid,
                position_id=pos_id,
                payload=pnl,
                dedupe_key=f"lm:exit_pnl_estimate:{pos_id}:{int(now//30)}",
            )
            if float(pnl.get("net_pnl_est") or 0.0) >= float(min_tp):
                append_position_journal(root, {"ts": now, "event": "take_profit_check_passed", "position_id": pos_id, "product_id": pid, "min_net_take_profit_usd": min_tp, "net_pnl_est": pnl.get("net_pnl_est")})
                exit_reason = "take_profit"
            else:
                append_position_journal(root, {"ts": now, "event": "take_profit_check_failed_below_threshold", "position_id": pos_id, "product_id": pid, "min_net_take_profit_usd": min_tp, "net_pnl_est": pnl.get("net_pnl_est")})
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

        # Normalize exit base size (snap DOWN to Coinbase increment).
        norm = None
        diag = {}
        try:
            from trading_ai.nte.execution.coinbase_min_notional import resolve_coinbase_min_notional_usd, refresh_coinbase_product_rules_cache
            from trading_ai.live_micro.exit_size import normalize_exit_base_size

            # Ensure cache row exists so base_increment/base_min_size can be read.
            refresh_coinbase_product_rules_cache(product_id=pid, runtime_root=root)
            _vmin, _src, meta = resolve_coinbase_min_notional_usd(product_id=pid, runtime_root=root, refresh_if_missing=False)
            inc = (meta or {}).get("base_increment")
            min_b = (meta or {}).get("base_min_size")
            norm, diag = normalize_exit_base_size(
                base_qty=float(str(base_qty).replace(",", "")),
                base_increment=inc,
                base_min_size=min_b,
            )
            append_position_journal(root, {"ts": now, "event": "exit_size_normalized", "position_id": pos_id, "product_id": pid, **diag})
            maybe_write_live_micro_event(
                runtime_root=root,
                event="exit_size_normalized",
                product_id=pid,
                position_id=pos_id,
                payload=diag,
                dedupe_key=f"lm:exit_size_normalized:{pos_id}:{exit_reason}",
            )
        except Exception:
            norm = None

        if not norm:
            append_position_journal(root, {"ts": now, "event": "exit_size_invalid_after_normalization", "position_id": pos_id, "product_id": pid, **(diag or {})})
            maybe_write_live_micro_event(
                runtime_root=root,
                event="exit_size_invalid_after_normalization",
                product_id=pid,
                position_id=pos_id,
                payload=diag or {},
                dedupe_key=f"lm:exit_size_invalid_after_normalization:{pos_id}:{exit_reason}",
            )
            continue

        # Submit market sell (normalized base size).
        res = client.place_market_sell(pid, norm, execution_gate="gate_b")
        exits_submitted += 1
        patch2 = {
            # If the exit failed, keep the position actionable (do not dead-end into "failed").
            "status": "closing" if res.success else "open",
            "exit_reason": exit_reason,
            "exit_order_id": (str(res.order_id or "").strip() if res.success else ""),
            "exit_submit_ts": now,
            "last_exit_error": (str(res.reason or "").strip() if not res.success else ""),
            "exit_attempts": int(_f(p.get("exit_attempts") or 0.0)) + (0 if res.success else 1),
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

