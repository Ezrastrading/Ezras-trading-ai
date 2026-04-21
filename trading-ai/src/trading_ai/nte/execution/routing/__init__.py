"""
Venue-agnostic spot routing + portfolio intelligence (universal core + Coinbase adapter).

Public entry points:
- :func:`trading_ai.nte.execution.routing.policy.runtime_coinbase_policy.build_runtime_coinbase_policy_snapshot`
- :func:`trading_ai.nte.execution.routing.integration.validation_resolve.resolve_validation_product_coherent`
"""

from trading_ai.nte.execution.routing.diagnostics.artifacts import (
    write_portfolio_truth_snapshot,
    write_product_graph_snapshot,
    write_route_search_diagnostics,
)
from trading_ai.nte.execution.routing.integration.validation_resolve import (
    merge_validation_candidates_for_runtime,
    resolve_validation_product_coherent,
)
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import (
    CoinbaseRuntimeProductPolicy,
    RuntimeCoinbasePolicySnapshot,
    build_runtime_coinbase_policy_snapshot,
    resolve_coinbase_runtime_product_policy,
    write_runtime_policy_artifacts,
)

__all__ = [
    "CoinbaseRuntimeProductPolicy",
    "RuntimeCoinbasePolicySnapshot",
    "build_runtime_coinbase_policy_snapshot",
    "resolve_coinbase_runtime_product_policy",
    "write_runtime_policy_artifacts",
    "merge_validation_candidates_for_runtime",
    "resolve_validation_product_coherent",
    "write_product_graph_snapshot",
    "write_route_search_diagnostics",
    "write_portfolio_truth_snapshot",
]
