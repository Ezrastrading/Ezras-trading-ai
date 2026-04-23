from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path.home().resolve() / "ezras-runtime"


def _cache_path(runtime_root: Optional[Path] = None) -> Path:
    root = (runtime_root or _runtime_root()).resolve()
    return root / "data" / "control" / "coinbase_product_rules_cache.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _extract_min_quote_from_product_meta(meta: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """
    Best-effort parsing across common Coinbase Advanced Trade product shapes.
    Returns (min_quote, field_name).
    """
    # Common/likely keys (string numbers).
    for k in ("quote_min_size", "min_market_funds", "min_notional", "min_quote_size"):
        v = _f(meta.get(k))
        if v is not None and v > 0:
            return v, k

    # Nested shapes (some endpoints return {value, currency})
    for k in ("quote_min_size", "min_market_funds"):
        node = meta.get(k)
        if isinstance(node, dict):
            v = _f(node.get("value"))
            if v is not None and v > 0:
                return v, f"{k}.value"

    # Fallback: no min found
    return None, None


def refresh_coinbase_product_rules_cache(
    *,
    product_id: str,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Fetch public product metadata and cache min-notional fields.
    Safe: does not require auth.
    """
    pid = (product_id or "").strip().upper()
    root = (runtime_root or _runtime_root()).resolve()
    p = _cache_path(root)
    cache = _read_json(p)
    rows = cache.get("products") if isinstance(cache.get("products"), dict) else {}
    if not isinstance(rows, dict):
        rows = {}
    try:
        from trading_ai.shark.outlets.coinbase import _brokerage_public_request

        meta = _brokerage_public_request(f"/market/products/{pid}")
        meta = meta if isinstance(meta, dict) else {}
        min_quote, field = _extract_min_quote_from_product_meta(meta)
        row = {
            "product_id": pid,
            "fetched_at_unix": time.time(),
            "min_quote_usd": min_quote,
            "min_quote_field": field,
            "raw_keys": sorted(list(meta.keys()))[:40],
        }
        rows[pid] = row
        out = {
            "truth_version": "coinbase_product_rules_cache_v1",
            "generated_at_unix": time.time(),
            "products": rows,
        }
        _write_json_atomic(p, out)
        return row
    except Exception as exc:
        logger.debug("refresh_coinbase_product_rules_cache failed", exc_info=True)
        return {"product_id": pid, "error": type(exc).__name__, "fetched_at_unix": time.time()}


def resolve_coinbase_min_notional_usd(
    *,
    product_id: str,
    runtime_root: Optional[Path] = None,
    refresh_if_missing: bool = True,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Returns (min_notional_usd, source, meta).
    source is one of:
    - coinbase_product_metadata_cache
    - coinbase_product_metadata_live_refresh
    - bundled_defaults_fallback
    """
    pid = (product_id or "").strip().upper()
    root = (runtime_root or _runtime_root()).resolve()
    cache = _read_json(_cache_path(root))
    products = cache.get("products") if isinstance(cache.get("products"), dict) else {}
    if isinstance(products, dict):
        row = products.get(pid)
        if isinstance(row, dict) and row.get("min_quote_usd") not in (None, "", 0, 0.0):
            try:
                return float(row["min_quote_usd"]), "coinbase_product_metadata_cache", row
            except Exception:
                pass
    if refresh_if_missing:
        row2 = refresh_coinbase_product_rules_cache(product_id=pid, runtime_root=root)
        if isinstance(row2, dict) and row2.get("min_quote_usd") not in (None, "", 0, 0.0):
            try:
                return float(row2["min_quote_usd"]), "coinbase_product_metadata_live_refresh", row2
            except Exception:
                pass

    # Last resort: conservative fallback.
    return 10.0, "bundled_defaults_fallback_10", {"product_id": pid}

