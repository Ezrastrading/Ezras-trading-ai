"""
Trade reports for Coinbase and Kalshi.
Separated by market, organized by time period.
Auto-exports to Excel. Used in CEO briefings 4x/day.
Claude reads this for all decisions.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from trading_ai.governance.storage_architecture import shark_state_path

REPORTS_FILE = shark_state_path("trade_reports.json")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_reports() -> dict:
    try:
        p = Path(REPORTS_FILE)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {
        "coinbase": {
            "all_trades": [],
            "stats": {
                "total_trades": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_pnl": 0.0,
                "best_trade": None,
                "worst_trade": None,
            },
        },
        "kalshi": {
            "all_trades": [],
            "stats": {
                "total_trades": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_pnl": 0.0,
                "best_trade": None,
                "worst_trade": None,
            },
        },
        "created_at": _now_utc(),
        "last_updated": _now_utc(),
    }


def record_trade(
    platform: str,
    gate: str,
    product_id: str,
    strategy: str,
    entry_price: float,
    exit_price: float,
    size_usd: float,
    pnl_usd: float,
    exit_reason: str,
    hold_seconds: int,
    balance_after: float,
) -> None:
    reports = _load_reports()
    trade = {
        "id": f"{platform}_{int(time.time())}",
        "timestamp": _now_utc(),
        "unix_ts": time.time(),
        "gate": gate,
        "product_id": product_id,
        "strategy": strategy,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "size_usd": size_usd,
        "pnl_usd": pnl_usd,
        "pnl_pct": (
            (exit_price - entry_price) / entry_price * 100 if entry_price else 0
        ),
        "exit_reason": exit_reason,
        "hold_seconds": hold_seconds,
        "hold_min": round(hold_seconds / 60, 1),
        "balance_after": balance_after,
        "win": pnl_usd >= 0,
    }

    if platform not in reports:
        reports[platform] = {
            "all_trades": [],
            "stats": {
                "total_trades": 0,
                "total_wins": 0,
                "total_losses": 0,
                "total_pnl": 0.0,
                "best_trade": None,
                "worst_trade": None,
            },
        }

    reports[platform]["all_trades"].append(trade)

    stats = reports[platform]["stats"]
    stats["total_trades"] = stats.get("total_trades", 0) + 1
    stats["total_pnl"] = stats.get("total_pnl", 0) + pnl_usd
    if pnl_usd >= 0:
        stats["total_wins"] = stats.get("total_wins", 0) + 1
    else:
        stats["total_losses"] = stats.get("total_losses", 0) + 1

    if not stats.get("best_trade") or pnl_usd > stats["best_trade"]["pnl_usd"]:
        stats["best_trade"] = trade
    if not stats.get("worst_trade") or pnl_usd < stats["worst_trade"]["pnl_usd"]:
        stats["worst_trade"] = trade

    reports["last_updated"] = _now_utc()

    p = Path(REPORTS_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reports, indent=2))


def _filter_by_period(trades: list, period: str) -> list:
    now = time.time()
    if period == "hour":
        cutoff = now - 3600
    elif period == "day":
        cutoff = now - 86400
    elif period == "week":
        cutoff = now - 604800
    elif period == "month":
        cutoff = now - 2592000
    else:
        cutoff = 0
    return [t for t in trades if t.get("unix_ts", 0) >= cutoff]


def get_platform_report(platform: str, period: str = "day") -> dict:
    reports = _load_reports()
    data = reports.get(platform, {})
    all_trades = data.get("all_trades", [])
    trades = _filter_by_period(all_trades, period)

    if not trades:
        return {
            "platform": platform,
            "period": period,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "pnl_usd": 0.0,
            "win_rate": 0.0,
            "avg_pnl_per_trade": 0.0,
        }

    wins = sum(1 for t in trades if t["win"])
    pnl = sum(t["pnl_usd"] for t in trades)

    by_gate: dict = {}
    for t in trades:
        g = t.get("gate", "unknown")
        if g not in by_gate:
            by_gate[g] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_gate[g]["trades"] += 1
        by_gate[g]["pnl"] += t["pnl_usd"]
        if t["win"]:
            by_gate[g]["wins"] += 1

    by_product: dict = {}
    for t in trades:
        pid = t.get("product_id", "unknown")
        if pid not in by_product:
            by_product[pid] = {"trades": 0, "pnl": 0.0}
        by_product[pid]["trades"] += 1
        by_product[pid]["pnl"] += t["pnl_usd"]

    top_products = sorted(by_product.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]

    return {
        "platform": platform,
        "period": period,
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": wins / len(trades),
        "pnl_usd": pnl,
        "avg_pnl_per_trade": pnl / len(trades),
        "by_gate": by_gate,
        "top_products": top_products,
        "best_trade": max(trades, key=lambda x: x["pnl_usd"]),
        "worst_trade": min(trades, key=lambda x: x["pnl_usd"]),
        "avg_hold_min": sum(t["hold_min"] for t in trades) / len(trades),
    }


def get_combined_report(period: str = "day") -> dict:
    cb = get_platform_report("coinbase", period)
    ka = get_platform_report("kalshi", period)

    total_pnl = cb.get("pnl_usd", 0) + ka.get("pnl_usd", 0)
    total_trades = cb.get("trades", 0) + ka.get("trades", 0)

    return {
        "period": period,
        "coinbase": cb,
        "kalshi": ka,
        "combined": {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "avg_pnl": (total_pnl / total_trades if total_trades else 0),
        },
    }


def format_report_for_telegram(period: str = "day") -> str:
    r = get_combined_report(period)
    cb = r["coinbase"]
    ka = r["kalshi"]
    co = r["combined"]

    period_label = {
        "hour": "LAST HOUR",
        "day": "TODAY",
        "week": "THIS WEEK",
        "month": "THIS MONTH",
    }.get(period, period.upper())

    cb_bt = cb.get("best_trade") or {}
    cb_wt = cb.get("worst_trade") or {}
    if isinstance(cb_bt, dict):
        cb_best = cb_bt.get("pnl_usd", 0)
        cb_worst = cb_wt.get("pnl_usd", 0)
    else:
        cb_best = 0
        cb_worst = 0

    return f"""
📊 {period_label} TRADE REPORT
{'─'*35}
🟡 COINBASE ({cb.get('trades',0)} trades)
  P&L: ${cb.get('pnl_usd',0):+.4f}
  Win Rate: {cb.get('win_rate',0)*100:.1f}%
  Avg/trade: ${cb.get('avg_pnl_per_trade',0):+.4f}
  Best: ${cb_best:+.4f}
  Worst: ${cb_worst:+.4f}

🔴 KALSHI ({ka.get('trades',0)} trades)
  P&L: ${ka.get('pnl_usd',0):+.4f}
  Win Rate: {ka.get('win_rate',0)*100:.1f}%
  Avg/trade: ${ka.get('avg_pnl_per_trade',0):+.4f}

💰 COMBINED
  Trades: {co.get('total_trades',0)}
  P&L: ${co.get('total_pnl',0):+.4f}
  Avg: ${co.get('avg_pnl',0):+.4f}
{'─'*35}"""


def export_to_excel_data() -> dict:
    """Returns structured data for Excel export."""
    reports = _load_reports()
    return {
        "coinbase_trades": reports.get("coinbase", {}).get("all_trades", []),
        "kalshi_trades": reports.get("kalshi", {}).get("all_trades", []),
        "generated_at": _now_utc(),
        "periods": {
            "hour": get_combined_report("hour"),
            "day": get_combined_report("day"),
            "week": get_combined_report("week"),
            "month": get_combined_report("month"),
        },
    }
