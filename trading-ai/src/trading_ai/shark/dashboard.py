"""Master dashboard — aggregate view across all avenues + treasury."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logger = logging.getLogger(__name__)


def get_master_dashboard() -> Dict[str, Any]:
    """
    Aggregate view across all avenues + treasury.

    Returns:
        {
          total_capital_deployed, total_current_value, total_profit,
          total_profit_pct, best_avenue, worst_avenue,
          avenues: {kalshi, manifold, polymarket, tastytrade, webull, sports_manual},
          treasury: {coinbase_usdc, coinbase_eth, total_withdrawn},
          month_4_projection, year_end_projection
        }
    """
    from trading_ai.shark.avenues import load_avenues

    avenues = load_avenues()

    total_deployed = sum(a.starting_capital for a in avenues.values())
    total_current = sum(a.current_capital for a in avenues.values())
    total_profit = sum(a.total_profit for a in avenues.values())
    total_profit_pct = round(
        (total_profit / max(total_deployed, 1e-9)) * 100, 2
    )

    # Best / worst by total_profit
    best = max(avenues, key=lambda k: avenues[k].total_profit) if avenues else ""
    worst = min(avenues, key=lambda k: avenues[k].total_profit) if avenues else ""

    # Treasury state
    treasury_data: Dict[str, Any] = {
        "coinbase_usdc": 0.0,
        "coinbase_eth": 0.0,
        "total_withdrawn": 0.0,
    }
    try:
        from trading_ai.shark.treasury import load_treasury
        t = load_treasury()
        treasury_data["total_withdrawn"] = t.get("total_withdrawn_usd", 0.0)
    except Exception:
        pass

    try:
        from trading_ai.shark.coinbase_tracker import get_coinbase_balance
        cb = get_coinbase_balance()
        treasury_data["coinbase_usdc"] = cb.get("usdc", 0.0)
        treasury_data["coinbase_eth"] = cb.get("eth_usd_value", 0.0)
    except Exception:
        pass

    # Projections: sum of each avenue's targets (all are MINIMUMS)
    month_4_projection = sum(a.month_4_target for a in avenues.values())
    month_6_projection = sum(a.month_6_target for a in avenues.values())
    year_end_projection = sum(a.year_end_target for a in avenues.values())

    return {
        "total_capital_deployed": round(total_deployed, 2),
        "total_current_value": round(total_current, 2),
        "total_profit": round(total_profit, 2),
        "total_profit_pct": total_profit_pct,
        "best_avenue": best,
        "worst_avenue": worst,
        "avenues": {
            k: {
                "name": a.name,
                "current_capital": a.current_capital,
                "total_profit": a.total_profit,
                "total_trades": a.total_trades,
                "win_rate": a.win_rate,
                "status": a.status,
                "automation_level": a.automation_level,
                "month_1_target": a.month_1_target,
                "month_4_target": a.month_4_target,
                "month_6_target": a.month_6_target,
                "year_end_target": a.year_end_target,
            }
            for k, a in avenues.items()
        },
        "treasury": treasury_data,
        "month_4_projection": round(month_4_projection, 2),
        "month_6_projection": round(month_6_projection, 2),
        "year_end_projection": round(year_end_projection, 2),
    }


def format_dashboard_message(dashboard: Dict[str, Any]) -> str:
    """Format master dashboard as a Telegram-ready summary."""
    lines = [
        "🦈 MASTER DASHBOARD",
        f"Total deployed: ${dashboard['total_capital_deployed']:.2f}",
        f"Total value:    ${dashboard['total_current_value']:.2f}",
        f"Total P&L:      ${dashboard['total_profit']:+.2f} ({dashboard['total_profit_pct']:+.1f}%)",
        "",
        "AVENUES:",
    ]
    for key, av in dashboard["avenues"].items():
        emoji = "🟢" if av["total_profit"] >= 0 else "🔴"
        lines.append(
            f"  {emoji} {av['name']}: ${av['current_capital']:.2f}"
            f" ({av['total_profit']:+.2f}) | {av['total_trades']} trades"
        )
    t = dashboard["treasury"]
    lines += [
        "",
        "TREASURY (Coinbase):",
        f"  USDC: ${t['coinbase_usdc']:.2f}",
        f"  ETH:  ${t['coinbase_eth']:.2f}",
        f"  Withdrawn: ${t['total_withdrawn']:.2f}",
        "",
        f"Month-4 target (MIN): ${dashboard['month_4_projection']:,.0f}",
        f"Month-6 target (MIN): ${dashboard['month_6_projection']:,.0f}",
        f"Year-end target (MIN): ${dashboard['year_end_projection']:,.0f}",
        "Targets: MINIMUM expectations. Faster is always better. 🦈",
    ]
    return "\n".join(lines)
