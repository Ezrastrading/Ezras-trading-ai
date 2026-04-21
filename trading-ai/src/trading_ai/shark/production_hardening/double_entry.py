"""Double-entry ledger lines (USD-normalized)."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Sequence


class AccountingMismatchAbort(Exception):
    pass


def layer_enabled() -> bool:
    return os.environ.get("PRODUCTION_HARDENING_LAYER", "0").strip() in ("1", "true", "yes")


def assert_double_entry_balanced(lines: Sequence[Dict[str, Any]]) -> None:
    deb = sum(float(x.get("usd_equiv") or 0) for x in lines if str(x.get("leg")) == "debit")
    cred = sum(float(x.get("usd_equiv") or 0) for x in lines if str(x.get("leg")) == "credit")
    if abs(deb - cred) > 1e-6:
        raise AccountingMismatchAbort(f"unbalanced deb={deb} cred={cred}")


def _append_jsonl(path: Any, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")


def append_trade_ledger(
    *,
    venue: str,
    order_id: str,
    client_order_id: str,
    product_or_market: str,
    lines: List[Dict[str, Any]],
) -> str:
    assert_double_entry_balanced(lines)
    rid = str(uuid.uuid4())
    from trading_ai.shark.production_hardening import paths as ph_paths

    _append_jsonl(
        ph_paths.trade_ledger_jsonl(),
        {
            "id": rid,
            "venue": venue,
            "order_id": order_id,
            "client_order_id": client_order_id,
            "product_or_market": product_or_market,
            "lines": lines,
            "ts": time.time(),
        },
    )
    return rid


def record_fill_from_execution(**kwargs: Any) -> Any:
    if not layer_enabled():
        return None
    _ = kwargs
    return {"ok": True}
