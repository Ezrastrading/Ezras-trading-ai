"""Routing integration: validation resolution, Gate hooks."""

from trading_ai.nte.execution.routing.integration.validation_resolve import (
    assert_validation_resolution_execution_invariant,
    resolve_validation_product_coherent,
    tuple_for_legacy_api,
)

__all__ = [
    "assert_validation_resolution_execution_invariant",
    "resolve_validation_product_coherent",
    "tuple_for_legacy_api",
]
