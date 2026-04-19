"""
Supabase reads — graceful when URL/key or tables missing.

Returns normalized dict + ``missing_sources`` notes (never raises for missing table).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def read_supabase_snapshot(
    *,
    trade_limit: int = 500,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "connected": False,
        "trades": [],
        "performance_rows": [],
        "missing_sources": [],
        "errors": [],
    }
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        out["missing_sources"].append("supabase_credentials")
        return out
    try:
        from supabase import create_client

        client = create_client(url, key)
        out["connected"] = True
    except Exception as exc:
        out["errors"].append(f"client:{exc}")
        out["missing_sources"].append("supabase_client")
        return out

    try:
        r = client.table("trades").select("*").limit(trade_limit).execute()  # type: ignore[union-attr]
        out["trades"] = list(r.data or [])
    except Exception as exc:
        out["missing_sources"].append("table:trades")
        out["errors"].append(f"trades:{exc}")

    for tbl in ("performance", "ceo_briefings", "platform_performance"):
        try:
            r2 = client.table(tbl).select("*").limit(100).execute()  # type: ignore[union-attr]
            out.setdefault("extra_tables", {})[tbl] = list(r2.data or [])
        except Exception:
            out["missing_sources"].append(f"table:{tbl}")

    return out
