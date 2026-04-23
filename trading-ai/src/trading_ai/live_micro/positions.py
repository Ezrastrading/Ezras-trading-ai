from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def open_positions_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "live_micro_open_positions.json"


def position_journal_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "live_micro_position_journal.jsonl"


def load_open_positions(runtime_root: Path) -> List[Dict[str, Any]]:
    doc = _read_json(open_positions_path(runtime_root))
    rows = doc.get("positions")
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(dict(r))
    return out


def save_open_positions(runtime_root: Path, positions: List[Dict[str, Any]]) -> None:
    now = time.time()
    payload = {
        "truth_version": "live_micro_open_positions_v1",
        "generated_at_unix": now,
        "positions": positions,
        "open_count": len([p for p in positions if str(p.get("status") or "").lower() in ("open", "closing")]),
    }
    _write_json_atomic(open_positions_path(runtime_root), payload)


def append_position_journal(runtime_root: Path, event: Dict[str, Any]) -> None:
    _append_jsonl(position_journal_path(runtime_root), event)


def quote_currency_for_product(product_id: str) -> str:
    pid = (product_id or "").strip().upper()
    if "-" in pid:
        return pid.split("-", 1)[1].strip().upper() or "USD"
    return "USD"


def reserved_quote_by_ccy(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in positions:
        st = str(p.get("status") or "").lower()
        if st not in ("pending_entry", "open", "closing"):
            continue
        qccy = quote_currency_for_product(str(p.get("product_id") or ""))
        spent = 0.0
        try:
            spent = float(p.get("quote_spent") or 0.0)
        except Exception:
            spent = 0.0
        out[qccy] = float(out.get(qccy, 0.0) or 0.0) + max(0.0, spent)
    return out


def open_position_exists_for_product(positions: List[Dict[str, Any]], product_id: str) -> bool:
    pid = (product_id or "").strip().upper()
    for p in positions:
        if str(p.get("product_id") or "").strip().upper() != pid:
            continue
        if str(p.get("status") or "").lower() in ("pending_entry", "open", "closing"):
            return True
    return False


def count_open_positions(positions: List[Dict[str, Any]]) -> int:
    return sum(1 for p in positions if str(p.get("status") or "").lower() in ("pending_entry", "open", "closing"))


def upsert_position(runtime_root: Path, position: Dict[str, Any]) -> None:
    pos = dict(position)
    pid = str(pos.get("position_id") or "").strip()
    if not pid:
        return
    positions = load_open_positions(runtime_root)
    replaced = False
    for i, p in enumerate(positions):
        if str(p.get("position_id") or "").strip() == pid:
            positions[i] = {**p, **pos}
            replaced = True
            break
    if not replaced:
        positions.append(pos)
    # keep stable size
    positions = positions[-200:]
    save_open_positions(runtime_root, positions)


def mark_position_closed(runtime_root: Path, position_id: str, patch: Dict[str, Any]) -> None:
    pid = str(position_id or "").strip()
    if not pid:
        return
    positions = load_open_positions(runtime_root)
    for i, p in enumerate(positions):
        if str(p.get("position_id") or "").strip() == pid:
            positions[i] = {**p, **patch, "status": "closed", "closed_ts": time.time()}
            break
    save_open_positions(runtime_root, positions)

