"""Kalshi early exit: take profit or stop-loss on open positions (non-crypto by default)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Position exit alert dedupe: position_id -> last_alert_timestamp
_POSITION_EXIT_ALERTS: Dict[str, float] = {}


def _should_send_exit_alert(position_id: str, min_interval_sec: float = 300) -> bool:
    """Check if exit alert should be sent (max 1 alert per position lifecycle)."""
    if not position_id:
        return False
    last_sent = _POSITION_EXIT_ALERTS.get(position_id, 0)
    now = time.time()
    if now - last_sent < min_interval_sec:
        logger.debug("exit alert deduped for position %s (last sent %.0f seconds ago)", position_id, now - last_sent)
        return False
    _POSITION_EXIT_ALERTS[position_id] = now
    return True


def _env_truthy(name: str, default: str = "false") -> bool:
    import os

    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def _parse_pct(name: str, default: float) -> float:
    import os

    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def run_kalshi_profit_exit_scan() -> None:
    """Poll Kalshi open positions; market-sell when +take-profit or -stop-loss vs entry."""
    if not _env_truthy("KALSHI_PROFIT_EXIT_ENABLED", "false"):
        return

    take_pct = _parse_pct("KALSHI_PROFIT_EXIT_PCT", 0.0015)
    stop_pct = _parse_pct("KALSHI_STOP_LOSS_PCT", 0.0012)
    exit_crypto = _env_truthy("KALSHI_PROFIT_EXIT_CRYPTO", "false")

    from trading_ai.shark.kalshi_crypto import kalshi_ticker_is_crypto
    from trading_ai.shark.outlets.kalshi import KalshiClient, _kalshi_yes_no_from_market_row
    from trading_ai.shark.reporting import send_telegram
    from trading_ai.shark.state_store import apply_win_loss_to_capital, load_positions, save_positions

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return

    data = load_positions()
    ops: List[Dict[str, Any]] = list(data.get("open_positions") or [])
    if not ops:
        return

    remaining: List[Dict[str, Any]] = []
    hist = list(data.get("history") or [])

    for p in ops:
        if str(p.get("outlet") or "").lower() != "kalshi":
            remaining.append(p)
            continue
        tid = str(p.get("market_id") or "").strip()
        if not tid:
            remaining.append(p)
            continue
        if kalshi_ticker_is_crypto(tid) and not exit_crypto:
            remaining.append(p)
            continue

        side = str(p.get("side") or "yes").lower()
        if side not in ("yes", "no"):
            side = "yes"
        try:
            entry = float(p.get("entry_price") or 0.0)
            shares = float(p.get("shares") or 0.0)
        except (TypeError, ValueError):
            remaining.append(p)
            continue
        if entry <= 0 or shares <= 0:
            remaining.append(p)
            continue

        try:
            mj = client.get_market(tid)
            inner = mj.get("market") if isinstance(mj.get("market"), dict) else mj
            if not isinstance(inner, dict):
                inner = {}
            y, n, _, _ = _kalshi_yes_no_from_market_row(inner)
        except Exception as exc:
            logger.debug("profit exit price fetch failed %s: %s", tid, exc)
            remaining.append(p)
            continue

        cur = y if side == "yes" else n
        gain = (cur - entry) / max(entry, 1e-9)
        pid = str(p.get("position_id") or "")
        opened = float(p.get("opened_at") or time.time())
        mins = max(0, int((time.time() - opened) / 60.0))

        exit_now = False
        tag = ""
        if gain >= take_pct:
            exit_now = True
            tag = "PROFIT EXIT"
        elif gain <= -stop_pct:
            exit_now = True
            tag = "STOP LOSS"

        if not exit_now:
            remaining.append(p)
            continue

        cnt = max(1, int(shares + 0.499))
        try:
            res = client.place_order(
                ticker=tid,
                side=side,
                count=cnt,
                action="sell",
            )
        except Exception as exc:
            logger.warning("kalshi profit exit sell failed %s: %s", tid, exc)
            remaining.append(p)
            continue

        fp = float(res.filled_price or cur)
        fs = float(res.filled_size or shares)
        pnl_est = float(fs) * (fp - entry) if fs > 0 else float(shares) * (cur - entry)

        entry_c = int(round(entry * 100))
        cur_c = int(round(cur * 100))
        gpct = gain * 100.0

        if tag == "PROFIT EXIT":
            logger.info(
                "💰 PROFIT EXIT: [%s] entry=%sc current=%sc gain=+%.2f%%",
                tid,
                entry_c,
                cur_c,
                gpct,
            )
            # Debounce: max 1 alert per position lifecycle
            if pid and _should_send_exit_alert(pid):
                send_telegram(
                    f"💰 PROFIT EXIT — {tid} +{gpct:.1f}% in {mins}min, ${pnl_est:.2f} profit"
                )
        else:
            logger.info(
                "🛑 STOP LOSS: [%s] entry=%sc current=%sc gain=%.2f%%",
                tid,
                entry_c,
                cur_c,
                gpct,
            )
            loss_amt = abs(pnl_est) if pnl_est < 0 else 0.0
            # Debounce: max 1 alert per position lifecycle
            if pid and _should_send_exit_alert(pid):
                send_telegram(
                    f"🛑 STOP LOSS — {tid} {gpct:.1f}%, cutting loss ${loss_amt:.2f}"
                )

        try:
            apply_win_loss_to_capital(pnl_est)
        except Exception as exc:
            logger.warning("apply_win_loss_to_capital failed after exit %s: %s", tid, exc)

        hist.append(
            {
                "position_id": pid,
                "outlet": "kalshi",
                "market_id": tid,
                "outcome": f"early_{tag.lower().replace(' ', '_')}",
                "pnl": pnl_est,
                "closed_at": time.time(),
                "hunt_types": [],
                "market_category": str(p.get("market_category") or ""),
            }
        )

    data["open_positions"] = remaining
    data["history"] = hist
    save_positions(data)
