"""
External strategy catalog — metadata only (no auto-live).

Delegates to :func:`read_external_candidates`; optional ``GLOBAL_EXTERNAL_SOURCES_PATH``.
"""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.global_layer.external_source_reader import read_external_candidates


def collect_candidates() -> Dict[str, Any]:
    """Return normalized external rows for ranking + synthesis (research firewall)."""
    return read_external_candidates()
