"""Crypto learning + execution intelligence (deterministic, artifact-driven).

Scope: Coinbase spot / BTC-first, but designed for extension to ETH + other products.
This layer is **additive**: it does not bypass live guards, governance, mission caps, cooldowns,
or exposure rules. It records evidence and computes advisory scores only.
"""

from trading_ai.intelligence.crypto_intelligence.distillation import write_daily_crypto_learning_distillation
from trading_ai.intelligence.crypto_intelligence.recorder import (
    record_gate_b_candidate_event,
    record_micro_candidate_decision,
)
from trading_ai.intelligence.crypto_intelligence.setup_family_stats import (
    update_setup_family_stats_from_trade_learning_object,
)

__all__ = [
    "record_gate_b_candidate_event",
    "record_micro_candidate_decision",
    "update_setup_family_stats_from_trade_learning_object",
    "write_daily_crypto_learning_distillation",
]
