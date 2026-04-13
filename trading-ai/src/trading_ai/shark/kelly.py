"""Binary contract Kelly: f* = (p*b - q) / b, b = net odds (profit/stake if win)."""

from __future__ import annotations


def net_odds_buy_yes(price: float) -> float:
    """Buy YES at `price` in (0,1): profit per $ staked if win."""
    if price <= 0 or price >= 1:
        return 0.0
    return (1.0 - price) / price


def kelly_full_fraction(p_win: float, price: float) -> float:
    """Full Kelly fraction of bankroll for buying YES at `price`."""
    b = net_odds_buy_yes(price)
    if b <= 0:
        return 0.0
    q = 1.0 - p_win
    raw = (p_win * b - q) / b
    return max(0.0, raw)


def apply_kelly_scaling(full_kelly_f: float, kelly_base: float) -> float:
    """kelly_base is e.g. 0.5 for half-Kelly, 0.8 for gap mode."""
    return max(0.0, full_kelly_f * kelly_base)
