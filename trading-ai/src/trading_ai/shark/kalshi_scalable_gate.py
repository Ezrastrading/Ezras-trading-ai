"""
Kalshi Scalable Gate — obvious-NO / mispriced-NO strategy.

Scans open markets, sizes from balance, requires Claude + GPT to agree that the YES
condition cannot realistically occur before expiry (not ROI/probability alone).

Legacy name ``kalshi_scalable_gate`` was previously Gate A; Gate A helpers were removed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Tuple

from trading_ai.llm.anthropic_defaults import DEFAULT_ANTHROPIC_MESSAGES_MODEL

logger = logging.getLogger(__name__)

MIN_NO_PROBABILITY = float(os.environ.get("KALSHI_SG_MIN_PROB", "0.90"))
MIN_PRICE_DISTANCE_PCT = float(os.environ.get("KALSHI_SG_MIN_DISTANCE_PCT", "0.10"))
MAX_CONTRACT_COST = float(os.environ.get("KALSHI_SG_MAX_CONTRACT_COST", "0.10"))
MAX_NO_ASK = float(os.environ.get("KALSHI_SG_MAX_NO_ASK", "0.99"))
MIN_CONTRACTS = int(os.environ.get("KALSHI_SG_MIN_CONTRACTS", "10"))
# Env often set to ``5.0`` meaning 500%% ROI; values ``> 50`` are treated as literal %%.
MIN_EXPECTED_ROI_PCT = float(os.environ.get("KALSHI_SG_MIN_ROI_PCT", "5.0"))
TTR_MIN_SECONDS = int(os.environ.get("KALSHI_SG_TTR_MIN", "300"))
TTR_MAX_SECONDS = int(os.environ.get("KALSHI_SG_TTR_MAX", "14400"))
ALLOCATION_PCT = float(os.environ.get("KALSHI_SG_ALLOCATION_PCT", "0.15"))
MAX_CONCURRENT_TRADES = int(os.environ.get("KALSHI_SG_MAX_CONCURRENT", "10"))
SCAN_INTERVAL_SECONDS = int(os.environ.get("KALSHI_SG_SCAN_INTERVAL", "300"))
MAX_MARKETS = int(os.environ.get("KALSHI_SG_MAX_MARKETS", "4000"))

# If true: filter on cheap NO asks (no_ask <= MAX_CONTRACT_COST) and lean on AI for edge.
CHEAP_NO_MODE = (os.environ.get("KALSHI_SG_CHEAP_NO") or "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

VALID_SERIES = (
    "KXINX",
    "KXSPX",
    "KXNDX",
    "KXBTCD",
    "KXETHD",
    "KXDOW",
    "KXGLD",
    "KXOIL",
)

_open_trades: Dict[str, Dict[str, Any]] = {}
_last_scan_time = 0.0
_daily_trades: List[Dict[str, Any]] = []
_daily_pnl = 0.0


def _min_roi_threshold_pct() -> float:
    v = MIN_EXPECTED_ROI_PCT
    return v * 100.0 if v <= 50.0 else v


def get_trade_size(balance: float) -> float:
    raw = balance * ALLOCATION_PCT
    return min(raw, 100.0)


def get_max_trades_per_hour(balance: float) -> int:
    if balance < 50:
        return 2
    if balance < 200:
        return 5
    if balance < 500:
        return 10
    return 20


def get_contracts(trade_size: float, contract_cost: float) -> int:
    if contract_cost <= 0:
        return 0
    n = int(trade_size / contract_cost)
    return max(MIN_CONTRACTS, n)


def _is_valid_series(ticker: str) -> bool:
    u = ticker.upper()
    return any(s in u for s in VALID_SERIES)


def _get_ttr(market: Dict[str, Any]) -> int:
    from trading_ai.shark.outlets.kalshi import _parse_close_timestamp_unix

    ts = _parse_close_timestamp_unix(market)
    if ts is None:
        return 0
    return max(0, int(ts - time.time()))


def _calculate_roi(
    contracts: int, cost_per_contract: float, probability: float
) -> Tuple[float, float]:
    total_cost = contracts * cost_per_contract
    max_payout = contracts * 1.0
    expected = max_payout * probability
    if total_cost <= 0:
        return 0.0, 0.0
    roi = (expected - total_cost) / total_cost * 100.0
    return roi, expected


def _get_sp500_estimate() -> float:
    """Approximate S&P 500 via Yahoo ``^GSPC`` last price."""
    try:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
            "?range=1d&interval=5m"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            j = json.loads(resp.read().decode())
        meta = j["chart"]["result"][0]["meta"]
        for key in ("regularMarketPrice", "chartPreviousClose", "previousClose"):
            v = meta.get(key)
            if v is not None:
                return float(v)
    except Exception as exc:
        logger.debug("S&P proxy fetch: %s", exc)
    return 0.0


def _validate_underlying_price(market: Dict[str, Any]) -> bool:
    ticker = str(market.get("ticker") or "")
    try:
        if "KXBTCD" in ticker.upper() or "KXBTC" in ticker.upper():
            from trading_ai.shark.outlets.coinbase import CoinbaseClient

            prices = CoinbaseClient().get_prices(["BTC-USD"])
            current = float(prices.get("BTC-USD", (0, 0))[0] or 0)
            if current <= 0:
                return True
            parts = ticker.split("-")
            last = parts[-1] if parts else ""
            if last.upper().startswith(("T", "B")):
                try:
                    threshold = float(last[1:].replace(".99", ""))
                    distance_pct = abs(current - threshold) / current
                    if distance_pct < MIN_PRICE_DISTANCE_PCT:
                        return False
                except ValueError:
                    pass

        elif "KXETHD" in ticker.upper() or "KXETH" in ticker.upper():
            from trading_ai.shark.outlets.coinbase import CoinbaseClient

            prices = CoinbaseClient().get_prices(["ETH-USD"])
            current = float(prices.get("ETH-USD", (0, 0))[0] or 0)
            if current <= 0:
                return True
            parts = ticker.split("-")
            last = parts[-1] if parts else ""
            if last.upper().startswith(("T", "B")):
                try:
                    threshold = float(last[1:].replace(".99", ""))
                    distance_pct = abs(current - threshold) / current
                    if distance_pct < MIN_PRICE_DISTANCE_PCT:
                        return False
                except ValueError:
                    pass
    except Exception as exc:
        logger.debug("Price validation: %s", exc)
    return True


def fetch_markets_for_gate(kalshi_client: Any) -> List[Dict[str, Any]]:
    from trading_ai.shark.kalshi_gate_b import _ensure_asks

    cap = max(200, min(20000, MAX_MARKETS))
    rows = kalshi_client.fetch_all_open_markets(max_rows=cap)
    out: List[Dict[str, Any]] = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        try:
            out.append(_ensure_asks(kalshi_client, m))
        except Exception:
            out.append(dict(m))
    return out


def _confirm_with_claude_gpt(market: Dict[str, Any], balance: float) -> Tuple[bool, str]:
    """
    Single question: can the YES condition realistically occur before expiry?
    Both models must approve ``approve_no_bet`` with ``physically_possible == false``
    and confidence not low. Missing API keys → reject.
    """
    ticker = str(market.get("ticker") or "")
    title = str(market.get("title") or market.get("subtitle") or "")
    ttr = _get_ttr(market)
    ttr_min = max(1, ttr // 60)

    current_prices: Dict[str, float] = {}
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        pr = CoinbaseClient().get_prices(["BTC-USD", "ETH-USD"])
        current_prices["BTC"] = float(pr.get("BTC-USD", (0, 0))[0] or 0)
        current_prices["ETH"] = float(pr.get("ETH-USD", (0, 0))[0] or 0)
    except Exception:
        pass

    spx = _get_sp500_estimate()
    price_context = ""
    tu = ticker.upper()
    if "BTC" in tu and current_prices.get("BTC", 0) > 0:
        price_context = f"Current BTC price: ${current_prices['BTC']:,.0f}"
    elif "ETH" in tu and current_prices.get("ETH", 0) > 0:
        price_context = f"Current ETH price: ${current_prices['ETH']:,.2f}"
    elif any(s in ticker for s in ("KXINX", "KXSPX", "KXNDX")):
        if spx > 0:
            price_context = f"Approx S&P 500 (Yahoo ^GSPC proxy): ${spx:,.2f}"
        else:
            price_context = "Index: verify whether the strike/range is reachable in the time left."

    prompt = f"""You are validating a prediction market trade.

MARKET: {title}
TIME TO RESOLVE: {ttr_min} minutes
BOOK BALANCE (hint): ${balance:.2f}
{price_context}

ONLY QUESTION: Will the YES condition actually happen in roughly {ttr_min} minutes?

We are betting NO (it will NOT happen). We need you to confirm it CANNOT happen.

Answer JSON only:
{{"approve_no_bet": true/false,
  "current_vs_target": "short text",
  "gap": "short text",
  "time_available": "{ttr_min} minutes",
  "physically_possible": true/false,
  "confidence": "high/medium/low",
  "reason": "one sentence max"}}

IMPORTANT:
approve_no_bet = true ONLY if you are CERTAIN the YES condition cannot happen.
If ANY doubt → approve_no_bet = false."""

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "Claude unavailable"
    if not os.environ.get("OPENAI_API_KEY"):
        return False, "GPT unavailable"

    # Claude
    try:
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=DEFAULT_ANTHROPIC_MESSAGES_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        s, e = text.find("{"), text.rfind("}") + 1
        if s < 0 or e <= s:
            return False, "Claude bad response"
        data = json.loads(text[s:e])
        claude_ok = bool(data.get("approve_no_bet", False))
        possible = bool(data.get("physically_possible", True))
        reason = str(data.get("reason") or "")
        conf = str(data.get("confidence") or "low").lower()
        if not claude_ok:
            return False, f"Claude: {reason}"
        if possible:
            return False, "Claude: YES still possible"
        if conf == "low":
            return False, "Claude: low confidence"
    except Exception as exc:
        logger.debug("Claude error: %s", exc)
        return False, "Claude unavailable"

    # GPT
    try:
        import openai

        gpt = openai.OpenAI()
        resp = gpt.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        s, e = text.find("{"), text.rfind("}") + 1
        if s < 0 or e <= s:
            return False, "GPT bad response"
        data = json.loads(text[s:e])
        gpt_ok = bool(data.get("approve_no_bet", False))
        possible = bool(data.get("physically_possible", True))
        reason = str(data.get("reason") or "")
        conf = str(data.get("confidence") or "low").lower()
        if not gpt_ok:
            return False, f"GPT: {reason}"
        if possible:
            return False, "GPT: YES still possible"
        if conf == "low":
            return False, "GPT: low confidence"
        final = f"Claude+GPT: {reason}"
        return True, final
    except Exception as exc:
        logger.debug("GPT error: %s", exc)
        return False, "GPT unavailable"


def scan_for_trades(markets: List[Dict[str, Any]], balance: float) -> List[Dict[str, Any]]:
    trade_size = get_trade_size(balance)
    candidates: List[Dict[str, Any]] = []

    for m in markets:
        ticker = str(m.get("ticker") or "")
        if not _is_valid_series(ticker):
            continue

        ya = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0.0)
        na = float(m.get("no_ask_dollars") or m.get("no_ask") or 0.0)
        if ya > 1.0:
            ya /= 100.0
        if na > 1.0:
            na /= 100.0
        if ya <= 0 or na <= 0:
            continue

        implied_no_wins = 1.0 - ya
        no_cost = na

        if CHEAP_NO_MODE:
            if no_cost <= 0 or no_cost > MAX_CONTRACT_COST:
                continue
            no_prob = implied_no_wins
        else:
            if implied_no_wins < MIN_NO_PROBABILITY:
                continue
            if ya > MAX_CONTRACT_COST:
                continue
            if no_cost > MAX_NO_ASK:
                continue
            no_prob = implied_no_wins

        ttr = _get_ttr(m)
        if not (TTR_MIN_SECONDS <= ttr <= TTR_MAX_SECONDS):
            continue

        if not _validate_underlying_price(m):
            continue

        contracts = get_contracts(trade_size, no_cost)
        total_cost = contracts * no_cost
        roi, expected = _calculate_roi(contracts, no_cost, no_prob)
        if roi < _min_roi_threshold_pct():
            continue

        candidates.append(
            {
                **m,
                "no_prob": no_prob,
                "no_cost": no_cost,
                "yes_ask": ya,
                "contracts": contracts,
                "total_cost": total_cost,
                "expected_payout": expected,
                "roi_pct": roi,
                "ttr": ttr,
                "trade_size": trade_size,
            }
        )

    candidates.sort(key=lambda x: -float(x.get("expected_payout") or 0.0))
    logger.info(
        "Scalable gate: %d candidates (markets=%d balance=$%.2f)",
        len(candidates),
        len(markets),
        balance,
    )
    return candidates


def place_trade(market: Dict[str, Any], kalshi_client: Any, balance: float) -> bool:
    global _open_trades, _daily_trades

    ticker = str(market["ticker"])
    contracts = int(market["contracts"])
    total_cost = float(market["total_cost"])
    expected = float(market["expected_payout"])
    roi = float(market["roi_pct"])
    no_prob = float(market["no_prob"])
    ttr = int(market["ttr"])

    if ticker in _open_trades:
        return False
    if len(_open_trades) >= MAX_CONCURRENT_TRADES:
        return False

    try:
        from trading_ai.shark.mission import evaluate_trade_against_mission

        check = evaluate_trade_against_mission(
            platform="kalshi",
            product_id=ticker,
            size_usd=total_cost,
            probability=no_prob,
            total_balance=balance,
        )
        if not check.get("approved", True):
            logger.info("Mission blocked: %s", check.get("reason"))
            return False
    except Exception:
        pass

    approved, reason = _confirm_with_claude_gpt(market, balance)
    if not approved:
        logger.info("AI rejected %s — %s", ticker, reason)
        return False

    try:
        result = kalshi_client.place_order(
            ticker=ticker,
            side="no",
            count=contracts,
            action="buy",
            order_type="market",
            skip_pretrade_buy_gates=True,
            min_order_prob=0.01,
        )
        if not result.success or not (result.order_id or "").strip():
            logger.warning("Order failed %s", ticker)
            return False

        _open_trades[ticker] = {
            "ticker": ticker,
            "contracts": contracts,
            "total_cost": total_cost,
            "expected_payout": expected,
            "roi_pct": roi,
            "no_prob": no_prob,
            "no_cost": float(market.get("no_cost") or 0),
            "ttr": ttr,
            "entry_time": time.time(),
            "order_id": result.order_id,
            "ai_reason": reason,
        }
        _daily_trades.append(dict(_open_trades[ticker]))

        try:
            from trading_ai.shark.supabase_logger import log_trade

            log_trade(
                platform="kalshi",
                gate="scalable",
                product_id=ticker,
                side="no",
                strategy="obvious_no",
                entry_price=float(market.get("no_cost") or 0),
                exit_price=0.0,
                size_usd=total_cost,
                pnl_usd=0.0,
                exit_reason="open",
                hold_seconds=0,
                balance_after=balance,
                metadata={"contracts": contracts, "roi_pct": roi, "ai": reason},
            )
        except Exception:
            pass

        try:
            from trading_ai.shark.reporting import send_telegram

            send_telegram(
                f"🎯 KALSHI SCALABLE\n{ticker}\nNO ×{contracts} ${total_cost:.2f} "
                f"exp ${expected:.2f} ROI {roi:.0f}%\n{reason[:200]}"
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.warning("place_trade %s: %s", ticker, exc)
        return False


def check_resolutions(kalshi_client: Any, balance: float) -> int:
    global _open_trades, _daily_pnl

    if not _open_trades:
        return 0

    resolved = 0
    for ticker in list(_open_trades.keys()):
        trade = _open_trades[ticker]
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
                age = time.time() - float(trade.get("entry_time") or 0.0)
                if age > float(trade.get("ttr") or 0.0) + 300:
                    del _open_trades[ticker]
                continue

            result = str(inner.get("result") or inner.get("yes_result") or "").strip().lower()
            won = result == "no"
            payout = float(trade["contracts"]) * 1.0 if won else 0.0
            pnl = payout - float(trade["total_cost"])
            _daily_pnl += pnl
            del _open_trades[ticker]
            resolved += 1

            logger.info(
                "SCALABLE RESOLVED: %s %s payout=$%.2f pnl=$%.2f",
                ticker,
                "WIN" if won else "LOSS",
                payout,
                pnl,
            )
            try:
                from trading_ai.shark.reporting import send_telegram

                emoji = "💰" if won else "❌"
                send_telegram(
                    f"{emoji} KALSHI SCALABLE RESULT\n{ticker}\n"
                    f"{'WIN' if won else 'LOSS'} PnL ${pnl:+.2f}"
                )
            except Exception:
                pass
            try:
                from trading_ai.shark.supabase_logger import log_trade

                log_trade(
                    platform="kalshi",
                    gate="scalable",
                    product_id=ticker,
                    side="no",
                    strategy="obvious_no",
                    entry_price=float(trade.get("no_cost") or 0),
                    exit_price=1.0 if won else 0.0,
                    size_usd=float(trade["total_cost"]),
                    pnl_usd=pnl,
                    exit_reason="win" if won else "loss",
                    hold_seconds=int(time.time() - float(trade["entry_time"])),
                    balance_after=balance + pnl,
                )
            except Exception:
                pass
        except Exception as exc:
            logger.debug("resolution %s: %s", ticker, exc)

    return resolved


def run_scalable_gate(kalshi_client: Any, balance: float) -> int:
    global _last_scan_time

    check_resolutions(kalshi_client, balance)

    now = time.time()
    if now - _last_scan_time < SCAN_INTERVAL_SECONDS:
        return 0
    _last_scan_time = now

    if not kalshi_client.has_kalshi_credentials():
        return 0

    markets = fetch_markets_for_gate(kalshi_client)
    candidates = scan_for_trades(markets, balance)
    if not candidates:
        return 0

    max_h = get_max_trades_per_hour(balance)
    capacity = min(MAX_CONCURRENT_TRADES, max(0, max_h - len(_open_trades)))
    if capacity <= 0:
        return 0

    placed = 0
    for cand in candidates[:capacity]:
        if place_trade(cand, kalshi_client, balance):
            placed += 1
            time.sleep(1.0)
    return placed


def get_status_report(balance: float) -> str:
    return (
        f"Kalshi scalable: open={len(_open_trades)}/{MAX_CONCURRENT_TRADES} "
        f"daily_pnl=${_daily_pnl:+.2f} size=${get_trade_size(balance):.2f} "
        f"max/hr≈{get_max_trades_per_hour(balance)}"
    )


def run_gate_a_job_fetch() -> int:
    """Deprecated: Gate A removed from this module. Use ``run_scalable_gate``."""
    logger.warning("run_gate_a_job_fetch is deprecated; enable KALSHI_SCALABLE_ENABLED instead")
    return 0
