"""Normalize external / internal citations into a common record shape."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional


def normalize_source(
    *,
    source_type: str,
    title: str,
    summary: str = "",
    url: Optional[str] = None,
    avenue_relevance: Optional[str] = None,
    strategy_family: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = f"{source_type}|{title}|{url or ''}"
    sid = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return {
        "source_id": sid,
        "source_type": source_type,
        "title": title,
        "summary": summary,
        "url": url or "",
        "avenue_relevance": avenue_relevance or "global",
        "strategy_family": strategy_family or "unknown",
        "execution_relevance": (extra or {}).get("execution_relevance", "unknown"),
        "credibility_indicators": (extra or {}).get("credibility_indicators", []),
        "testability": (extra or {}).get("testability", "unknown"),
    }
