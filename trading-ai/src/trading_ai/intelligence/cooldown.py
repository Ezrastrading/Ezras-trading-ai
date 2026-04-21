"""Minimum spacing between trade attempts (anti-overtrading)."""

import time

_last_trade_time = 0.0


def cooldown_active() -> bool:
    global _last_trade_time
    now = time.time()
    if now - _last_trade_time < 60:
        return True
    _last_trade_time = now
    return False
