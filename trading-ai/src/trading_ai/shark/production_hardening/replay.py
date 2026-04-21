"""Replay validation over persisted trade ledger JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from trading_ai.shark.production_hardening.double_entry import assert_double_entry_balanced


def trade_ledger_jsonl() -> Path:
    from trading_ai.shark.production_hardening import paths as ph_paths

    return ph_paths.trade_ledger_jsonl()


def run_replay_validation(*, last_n: int = 100) -> Dict[str, Any]:
    p = trade_ledger_jsonl()
    if not p.is_file():
        return {"ok": True, "rows": 0}
    lines = p.read_text(encoding="utf-8").strip().splitlines()[-last_n:]
    ok = True
    for ln in lines:
        try:
            rec = json.loads(ln)
            assert_double_entry_balanced(rec.get("lines") or [])
        except Exception:
            ok = False
    return {"ok": ok, "rows": len(lines)}
