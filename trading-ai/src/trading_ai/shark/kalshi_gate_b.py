"""Kalshi Gate B — any market, YES or NO, min probability & ROI; Claude+GPT confirmation.

State: ``shark/state/kalshi_gate_b_state.json``. Enable ``KALSHI_GATE_B_ENABLED=true``.
Open positions are tracked for resolution checks shared with Gate A.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.llm.anthropic_defaults import DEFAULT_ANTHROPIC_MESSAGES_MODEL

logger = logging.getLogger(__name__)

MIN_PROBABILITY = float(os.environ.get("KALSHI_GB_MIN_PROB", "0.90"))
MIN_ROI_PCT = float(os.environ.get("KALSHI_GB_MIN_ROI", "20.0"))
MAX_CONTRACT_COST = float(os.environ.get("KALSHI_GB_MAX_CONTRACT_COST", "0.80"))
MIN_CONTRACT_COST = float(os.environ.get("KALSHI_GB_MIN_CONTRACT_COST", "0.01"))
MIN_CONTRACTS = int(os.environ.get("KALSHI_GB_MIN_CONTRACTS", "5"))
TTR_MIN = int(os.environ.get("KALSHI_GB_TTR_MIN", "60"))
TTR_MAX = int(os.environ.get("KALSHI_GB_TTR_MAX", "604800"))  # 1 week (7 days)
ALLOCATION_PCT = float(os.environ.get("KALSHI_GB_ALLOCATION_PCT", "0.15"))
MAX_CONCURRENT = int(os.environ.get("KALSHI_GB_MAX_CONCURRENT", "20"))
SCAN_INTERVAL = float(os.environ.get("KALSHI_GB_SCAN_INTERVAL", "60"))

logger.info("Gate B config: MIN_PROB=%s MIN_ROI=%s TTR_MIN=%s TTR_MAX=%s", MIN_PROBABILITY, MIN_ROI_PCT, TTR_MIN, TTR_MAX)

SKIP_SUBSTR = tuple(
    x.strip().upper()
    for x in (os.environ.get("KALSHI_GB_SKIP_TICKER_SUBSTR") or "KXNBA-26,KXMLB-26").split(",")
    if x.strip()
)


def _state_path() -> Path:
    return shark_state_path("kalshi_gate_b_state.json")


def _default_state() -> Dict[str, Any]:
    return {
        "open_trades": {},
        "daily_pnl": 0.0,
        "last_scan": 0.0,
        "hour_key": "",
        "trades_this_hour": 0,
    }


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            s = _default_state()
            s.update(raw)
            return s
    except Exception as exc:
        logger.warning("kalshi_gate_b_state load: %s", exc)
    return _default_state()


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("kalshi_gate_b_state save: %s", exc)


def _available_balance() -> float:
    try:
        from trading_ai.shark.capital_effective import effective_capital_for_outlet
        from trading_ai.shark.state_store import load_capital

        book = load_capital()
        return max(0.0, effective_capital_for_outlet("kalshi", float(book.current_capital)))
    except Exception:
        raw = (os.environ.get("KALSHI_ACTUAL_BALANCE") or "0").strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 0.0


def get_trade_size(balance: float) -> float:
    return min(balance * ALLOCATION_PCT, 100.0)


def get_max_per_hour(balance: float) -> int:
    if balance < 50:
        return 5
    if balance < 200:
        return 10
    if balance < 500:
        return 20
    return 50


def _roi_pct(cost: float) -> float:
    if cost <= 0:
        return 0.0
    return (1.0 - cost) / cost * 100.0


def _get_ttr_sec(market: Dict[str, Any]) -> int:
    from trading_ai.shark.outlets.kalshi import _parse_close_timestamp_unix

    ts = _parse_close_timestamp_unix(market)
    if ts is None:
        return 0
    return max(0, int(ts - time.time()))


def _ensure_asks(client: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    from trading_ai.shark.outlets.kalshi import (
        _kalshi_yes_no_ask_from_market_row,
        fetch_kalshi_orderbook_best_ask_cents,
    )

    m = dict(row)
    ya, na, _, _ = _kalshi_yes_no_ask_from_market_row(m)
    tid = str(m.get("ticker") or "").strip()
    if (ya is None or na is None) and tid:
        yc, nc = fetch_kalshi_orderbook_best_ask_cents(tid, client)
        if ya is None and yc is not None:
            m["yes_ask_dollars"] = yc / 100.0
        if na is None and nc is not None:
            m["no_ask_dollars"] = nc / 100.0
    return m


def _analyze_market(market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ticker = str(market.get("ticker") or "")
    tu = ticker.upper()
    if any(s in tu for s in SKIP_SUBSTR if s):
        return None

    close_str = str(market.get("close_time") or "")
    # Removed year filter - we want short-term markets regardless of year
    # if any(y in close_str for y in ("2027", "2028", "2029")):
    #     return None

    status = str(market.get("status") or "").strip().lower()
    if status and status not in ("open", "active", ""):
        return None

    yes_ask = float(market.get("yes_ask_dollars") or market.get("yes_ask") or 0.0)
    no_ask = float(market.get("no_ask_dollars") or market.get("no_ask") or 0.0)
    if yes_ask > 1.0:
        yes_ask /= 100.0
    if no_ask > 1.0:
        no_ask /= 100.0

    if yes_ask <= 0 or no_ask <= 0:
        return None

    best_side: Optional[str] = None
    best_cost = 0.0
    best_prob = 0.0
    best_roi = -1.0

    if (
        yes_ask >= MIN_PROBABILITY
        and MIN_CONTRACT_COST <= yes_ask <= MAX_CONTRACT_COST
    ):
        r = _roi_pct(yes_ask)
        if r >= MIN_ROI_PCT:
            best_side = "yes"
            best_cost = yes_ask
            best_prob = yes_ask
            best_roi = r

    if (
        no_ask >= MIN_PROBABILITY
        and MIN_CONTRACT_COST <= no_ask <= MAX_CONTRACT_COST
    ):
        r = _roi_pct(no_ask)
        if r >= MIN_ROI_PCT and r > best_roi:
            best_side = "no"
            best_cost = no_ask
            best_prob = no_ask
            best_roi = r

    if not best_side:
        return None

    ttr = _get_ttr_sec(market)
    if not (TTR_MIN <= ttr <= TTR_MAX):
        return None

    return {
        "ticker": ticker,
        "title": str(market.get("title") or market.get("subtitle") or ""),
        "side": best_side,
        "probability": best_prob,
        "contract_cost": best_cost,
        "roi_pct": best_roi,
        "ttr": ttr,
        "market": market,
    }


def _analyze_market_debug(market: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    """Debug version with detailed logging to see why markets are rejected."""
    ticker = str(market.get("ticker") or "")
    tu = ticker.upper()
    if any(s in tu for s in SKIP_SUBSTR if s):
        return None

    close_str = str(market.get("close_time") or "")
    # Removed year filter - we want short-term markets regardless of year
    # if any(y in close_str for y in ("2027", "2028", "2029")):
    #     return None

    status = str(market.get("status") or "").strip().lower()
    if status and status not in ("open", "active", ""):
        return None

    yes_ask = float(market.get("yes_ask_dollars") or market.get("yes_ask") or 0.0)
    no_ask = float(market.get("no_ask_dollars") or market.get("no_ask") or 0.0)
    if yes_ask > 1.0:
        yes_ask /= 100.0
    if no_ask > 1.0:
        no_ask /= 100.0

    if yes_ask <= 0 or no_ask <= 0:
        return None

    best_side: Optional[str] = None
    best_cost = 0.0
    best_prob = 0.0
    best_roi = -1.0

    if (
        yes_ask >= MIN_PROBABILITY
        and MIN_CONTRACT_COST <= yes_ask <= MAX_CONTRACT_COST
    ):
        r = _roi_pct(yes_ask)
        if r >= MIN_ROI_PCT:
            best_side = "yes"
            best_cost = yes_ask
            best_prob = yes_ask
            best_roi = r

    if (
        no_ask >= MIN_PROBABILITY
        and MIN_CONTRACT_COST <= no_ask <= MAX_CONTRACT_COST
    ):
        r = _roi_pct(no_ask)
        if r >= MIN_ROI_PCT and r > best_roi:
            best_side = "no"
            best_cost = no_ask
            best_prob = no_ask
            best_roi = r

    if not best_side:
        return None

    ttr = _get_ttr_sec(market)
    if not (TTR_MIN <= ttr <= TTR_MAX):
        return None

    return {
        "ticker": ticker,
        "title": str(market.get("title") or market.get("subtitle") or ""),
        "side": best_side,
        "probability": best_prob,
        "contract_cost": best_cost,
        "roi_pct": best_roi,
        "ttr": ttr,
        "market": market,
    }


def _confirm_trade(trade_info: Dict[str, Any], balance: float) -> Tuple[bool, str]:
    """Claude + GPT JSON approve; both must approve with high confidence."""
    ticker = trade_info["ticker"]
    title = trade_info["title"]
    side = trade_info["side"]
    prob = float(trade_info["probability"])
    cost = float(trade_info["contract_cost"])
    ttr = int(trade_info["ttr"])
    roi = float(trade_info["roi_pct"])
    ttr_min = max(1, ttr // 60)

    price_context = ""
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        if "BTC" in ticker.upper():
            c = CoinbaseClient()
            pr = c.get_prices(["BTC-USD"])
            t = pr.get("BTC-USD")
            if t:
                bid, ask = t
                px = (bid + ask) / 2.0 if bid and ask else max(bid, ask)
                if px:
                    price_context = f"Current BTC: ${px:,.0f}"
        elif "ETH" in ticker.upper():
            c = CoinbaseClient()
            pr = c.get_prices(["ETH-USD"])
            t = pr.get("ETH-USD")
            if t:
                bid, ask = t
                px = (bid + ask) / 2.0 if bid and ask else max(bid, ask)
                if px:
                    price_context = f"Current ETH: ${px:,.2f}"
    except Exception:
        pass

    if side == "yes":
        question = f"Will this happen within roughly {ttr_min} minutes?\n'{title}'"
        we_win_if = "YES settles yes"
    else:
        question = f"Will YES fail / NO win within roughly {ttr_min} minutes?\n'{title}'"
        we_win_if = "NO settles (YES does not)"

    prompt = f"""Kalshi prediction market trade.

QUESTION: {question}
{price_context}

WE BET: {side.upper()} side
WE WIN IF: {we_win_if}
CONTRACT COST: ${cost:.2f} each
PAYOUT: $1.00 each if correct
ROI: {roi:.0f}%
TIME LEFT: ~{ttr_min} minutes
MARKET IMPLIED (cost as fraction): {prob*100:.0f}%

Answer JSON only:
{{"approve": true/false,
  "will_happen": true/false,
  "confidence": "high/medium/low",
  "reason": "one sentence",
  "risk": "low/medium/high"}}

Rules: approve=true only if confidence=high; if doubt → approve=false."""

    claude_ok = False
    reason = ""

    try:
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic

            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=DEFAULT_ANTHROPIC_MESSAGES_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text
            s, e = text.find("{"), text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                claude_ok = bool(data.get("approve"))
                reason = str(data.get("reason") or "")
                if not claude_ok:
                    return False, f"Claude rejected: {reason}"
                if str(data.get("confidence") or "").lower() != "high":
                    return False, "Claude not high confidence"
                if str(data.get("risk") or "").lower() == "high":
                    return False, "Claude high risk"
        else:
            return False, "No ANTHROPIC_API_KEY"
    except Exception as exc:
        logger.debug("Claude error: %s", exc)
        return False, f"Claude error: {exc}"

    try:
        if os.environ.get("OPENAI_API_KEY"):
            import openai

            gpt = openai.OpenAI()
            resp = gpt.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = (resp.choices[0].message.content or "")
            s, e = text.find("{"), text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                gpt_ok = bool(data.get("approve"))
                gpt_reason = str(data.get("reason") or "")
                if not gpt_ok:
                    return False, f"GPT rejected: {gpt_reason}"
                if str(data.get("confidence") or "").lower() != "high":
                    return False, "GPT not high confidence"
                if str(data.get("risk") or "").lower() == "high":
                    return False, "GPT high risk"
        else:
            return False, "No OPENAI_API_KEY"
    except Exception as exc:
        logger.debug("GPT error: %s", exc)
        return False, f"GPT error: {exc}"

    return True, f"✅ Both confirmed: {reason}"


def scan_markets(markets: List[Dict[str, Any]], balance: float) -> List[Dict[str, Any]]:
    trade_size = get_trade_size(balance)
    candidates: List[Dict[str, Any]] = []
    
    # Debug: log first 10 markets to see why they're rejected
    debug_count = 0
    for m in markets:
        if not isinstance(m, dict):
            continue
        r = _analyze_market(m)
        if not r:
            if debug_count < 10:
                debug_count += 1
                ticker = str(m.get("ticker") or "")
                yes_ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0.0)
                no_ask = float(m.get("no_ask_dollars") or m.get("no_ask") or 0.0)
                if yes_ask > 1.0:
                    yes_ask /= 100.0
                if no_ask > 1.0:
                    no_ask /= 100.0
                logger.info("Market %d rejected: ticker=%s yes_ask=%s no_ask=%s", debug_count, ticker, yes_ask, no_ask)
            continue
        candidates.append(
            {
                "ticker": r["ticker"],
                "title": r["title"],
                "side": r["side"],
                "probability": r["probability"],
                "contract_cost": r["contract_cost"],
                "roi_pct": r["roi_pct"],
                "ttr": r["ttr"],
                "trade_size": trade_size,
                "balance": balance,
            }
        )
    logger.info("Gate B scan debug: checked %d markets, found %d candidates", len(markets), len(candidates))
    
    candidates.sort(key=lambda x: (x["ttr"], -x["probability"], -x["roi_pct"]))  # Sort by TTR (shorter first), then prob, then ROI
    logger.info("Gate B scan: %d candidates from %d markets", len(candidates), len(markets))
    return candidates


def place_gate_b_trade(
    trade: Dict[str, Any],
    kalshi_client: Any,
    balance: float,
    state: Dict[str, Any],
) -> bool:
    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(kalshi_client, KalshiClient):
        return False

    ticker = trade["ticker"]
    side = str(trade["side"]).lower()
    contracts = int(trade["contracts"])
    total_cost = float(trade["total_cost"])
    expected = float(trade["expected_payout"])
    roi = float(trade["roi_pct"])
    prob = float(trade["probability"])
    ttr = int(trade["ttr"])
    title = str(trade["title"])

    open_trades: Dict[str, Any] = dict(state.get("open_trades") or {})
    if ticker in open_trades:
        return False
    if len(open_trades) >= MAX_CONCURRENT:
        return False

    approved, reason = _confirm_trade(trade, balance)
    if not approved:
        logger.info("Gate B SKIP %s: %s", ticker, reason)
        return False

    logger.info(
        "Gate B PLACING: %s %s × %d ~$%.2f (ROI ~%.0f%%)",
        ticker,
        side.upper(),
        contracts,
        total_cost,
        roi,
    )
    try:
        res = kalshi_client.place_order(
            ticker=ticker,
            side=side,
            count=contracts,
            action="buy",
            order_type="market",
            skip_pretrade_buy_gates=True,
            min_order_prob=0.01,
        )
        oid = str(res.order_id or "")
        if not res.success or not oid:
            return False

        open_trades[ticker] = {
            "ticker": ticker,
            "title": title,
            "side": side,
            "contracts": contracts,
            "contract_cost": trade["contract_cost"],
            "total_cost": total_cost,
            "expected_payout": expected,
            "roi_pct": roi,
            "probability": prob,
            "ttr": ttr,
            "entry_time": time.time(),
            "order_id": oid,
            "gate": "B",
        }
        state["open_trades"] = open_trades
        state["trades_this_hour"] = int(state.get("trades_this_hour") or 0) + 1
        _save_state(state)

        try:
            from trading_ai.shark.supabase_logger import log_trade

            log_trade(
                platform="kalshi",
                gate="B",
                product_id=ticker,
                side=side,
                strategy="gate_b",
                entry_price=float(trade["contract_cost"]),
                exit_price=0.0,
                size_usd=total_cost,
                pnl_usd=0.0,
                exit_reason="open",
                hold_seconds=0,
                balance_after=balance,
                metadata={
                    "contracts": contracts,
                    "roi_pct": roi,
                    "prob": prob,
                    "ttr_min": ttr // 60,
                    "ai_approval": reason[:500],
                },
            )
        except Exception:
            pass

        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(
                f"🎯 KALSHI GATE B\n"
                f"{'─'*32}\n"
                f"Market: {title[:60]}\n"
                f"Bet: {side.upper()} ({prob*100:.0f}% implied)\n"
                f"Contracts: {contracts}\n"
                f"Cost/contract: ${trade['contract_cost']:.2f}\n"
                f"Total cost: ${total_cost:.2f}\n"
                f"ROI: {roi:.0f}%\n"
                f"Resolves: ~{ttr//60} min\n"
                f"AI: {reason[:200]}\n"
                f"Open: {len(open_trades)}/{MAX_CONCURRENT}"
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.warning("Gate B place error %s: %s", ticker, exc)
        return False


def check_resolutions(kalshi_client: Any, balance: float) -> int:
    """Settle open Gate A/B positions when markets finalize."""
    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(kalshi_client, KalshiClient):
        return 0

    state = _load_state()
    open_trades: Dict[str, Any] = dict(state.get("open_trades") or {})
    if not open_trades:
        return 0

    resolved = 0
    for ticker in list(open_trades.keys()):
        tr = open_trades[ticker]
        try:
            mj = kalshi_client.get_market(ticker)
            inner = mj.get("market") if isinstance(mj.get("market"), dict) else mj
            if not isinstance(inner, dict):
                inner = {}
            st = str(inner.get("status") or "").strip().lower()
            terminal = st in (
                "finalized",
                "settled",
                "closed",
                "determined",
                "expired",
            ) or bool(inner.get("settled") or inner.get("is_settled"))

            if not terminal:
                age = time.time() - float(tr.get("entry_time") or 0.0)
                if age > float(tr.get("ttr") or 0.0) + 3600:
                    logger.info("Gate A/B: dropping stale open %s (no settlement)", ticker)
                    del open_trades[ticker]
                continue

            result = str(inner.get("result") or inner.get("yes_result") or "").strip().lower()
            side = str(tr.get("side") or "").lower()
            won = (side == "yes" and result == "yes") or (side == "no" and result == "no")
            contracts = float(tr.get("contracts") or 0.0)
            cost = float(tr.get("total_cost") or 0.0)
            payout = contracts * 1.0 if won else 0.0
            pnl = payout - cost
            state["daily_pnl"] = float(state.get("daily_pnl") or 0.0) + pnl
            del open_trades[ticker]
            state["open_trades"] = open_trades
            _save_state(state)
            resolved += 1
            logger.info(
                "Gate A/B RESOLVED: %s %s pnl=$%.2f",
                ticker,
                "WIN" if won else "LOSS",
                pnl,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                emoji = "💰" if won else "❌"
                send_telegram(
                    f"{emoji} KALSHI GATE {tr.get('gate', '?')} RESULT\n"
                    f"{tr.get('title', '')[:60]}\n"
                    f"{'WIN' if won else 'LOSS'} PnL ${pnl:+.2f}"
                )
            except Exception:
                pass
        except Exception as exc:
            logger.debug("resolution %s: %s", ticker, exc)

    return resolved


def run_gate_b(kalshi_client: Any, markets: List[Dict[str, Any]], balance: float) -> int:
    """Main Gate B cycle: resolve → rate-limit → scan → place (AI confirmed)."""
    if (os.environ.get("KALSHI_GATE_B_ENABLED") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return 0

    from trading_ai.shark.outlets.kalshi import KalshiClient

    if not isinstance(kalshi_client, KalshiClient) or not kalshi_client.has_kalshi_credentials():
        return 0

    state = _load_state()
    now = time.time()
    if now - float(state.get("last_scan") or 0.0) < SCAN_INTERVAL:
        return 0
    state["last_scan"] = now

    hk = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    if state.get("hour_key") != hk:
        state["hour_key"] = hk
        state["trades_this_hour"] = 0

    resolved = check_resolutions(kalshi_client, balance)
    max_h = get_max_per_hour(balance)
    if int(state.get("trades_this_hour") or 0) >= max_h:
        _save_state(state)
        return resolved

    capacity = MAX_CONCURRENT - len(state.get("open_trades") or {})
    if capacity <= 0:
        _save_state(state)
        return resolved

    enriched: List[Dict[str, Any]] = []
    for m in markets[:8000]:
        if not isinstance(m, dict):
            continue
        enriched.append(_ensure_asks(kalshi_client, m))

    candidates = scan_markets(enriched, balance)
    placed = 0
    for c in candidates:
        if capacity <= 0:
            break
        state = _load_state()
        if int(state.get("trades_this_hour") or 0) >= max_h:
            break
        if place_gate_b_trade(c, kalshi_client, balance, state):
            placed += 1
            capacity -= 1
            time.sleep(0.35)

    if placed or resolved:
        logger.info(
            "Gate B: placed=%d resolved=%d open=%d",
            placed,
            resolved,
            len(_load_state().get("open_trades") or {}),
        )
    return placed + resolved


def run_gate_b_job_fetch() -> int:
    """Fetch all open markets and run Gate B (for scheduler)."""
    from trading_ai.shark.outlets.kalshi import KalshiClient

    cap = max(500, min(12000, int(os.environ.get("KALSHI_GB_MAX_MARKETS", "5000"))))
    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return 0
    markets = client.fetch_all_open_markets(max_rows=cap)
    bal = _available_balance()
    return run_gate_b(client, markets, bal)
