"""
Multi-avenue universal intelligence layer — scoped registries, audits, and artifact namespaces.

Execution and venue-specific mechanics stay in their modules; this package provides:
- canonical scope / namespace keys
- avenue + gate registries and snapshots
- honest universalization audit + multi-avenue status matrix
- contamination guards and scoped path helpers
- framework hooks for CEO / progression / scanners (no fake execution)
"""

from __future__ import annotations

from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle

__all__ = [
    "write_multi_avenue_control_bundle",
]
