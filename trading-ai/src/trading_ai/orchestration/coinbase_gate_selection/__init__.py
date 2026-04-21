"""Coinbase Gate A/B product selection and capital split helpers (artifacts + deterministic ranking)."""

from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import (
    load_gate_a_product_policy,
    run_gate_a_product_selection,
)

__all__ = ["load_gate_a_product_policy", "run_gate_a_product_selection"]
