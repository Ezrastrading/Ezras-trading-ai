"""
External knowledge ingestion — **metadata only** by default (no auto-fetch in prod).

Set ``GLOBAL_EXTERNAL_SOURCES_PATH`` to a JSON file of
``[{ "source_type": "official_doc", "title": "...", "url": "...", "summary": "..." }]``
or use :func:`load_curated_defaults` for offline bootstrap.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.global_layer.source_normalizer import normalize_source

logger = logging.getLogger(__name__)


def load_curated_defaults() -> List[Dict[str, Any]]:
    """High-signal references (Hummingbot, venue docs) — not fetched, just catalogued."""
    return [
        normalize_source(
            source_type="framework",
            title="Hummingbot — market making & execution reference",
            summary="Open-source venue-aware execution patterns; sandbox before live.",
            url="https://github.com/hummingbot/hummingbot",
            strategy_family="market_making",
            extra={"credibility_indicators": ["oss", "execution_detail"]},
        ),
        normalize_source(
            source_type="official_doc",
            title="Coinbase Advanced Trade API",
            summary="REST + WS market data and user channels.",
            url="https://docs.cloud.coinbase.com/advanced-trade-api/docs/welcome",
            avenue_relevance="coinbase",
            extra={"credibility_indicators": ["official"]},
        ),
    ]


def read_external_candidates() -> Dict[str, Any]:
    path = (os.environ.get("GLOBAL_EXTERNAL_SOURCES_PATH") or "").strip()
    items: List[Dict[str, Any]] = []
    if path and Path(path).is_file():
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    items.append(
                        normalize_source(
                            source_type=str(row.get("source_type") or "other"),
                            title=str(row.get("title") or "untitled"),
                            summary=str(row.get("summary") or ""),
                            url=row.get("url"),
                            avenue_relevance=row.get("avenue_relevance"),
                            strategy_family=row.get("strategy_family"),
                            extra=row.get("extra") if isinstance(row.get("extra"), dict) else None,
                        )
                    )
        except Exception as exc:
            logger.warning("external sources file: %s", exc)
    if not items:
        items = load_curated_defaults()
    return {"candidates": items, "count": len(items)}
