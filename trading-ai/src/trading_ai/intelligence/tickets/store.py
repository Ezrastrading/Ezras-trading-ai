"""Append-only ticket storage and index maintenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trading_ai.intelligence.paths import (
    open_tickets_json_path,
    ticket_summary_txt_path,
    tickets_jsonl_path,
)
from trading_ai.intelligence.tickets.models import Ticket, TicketStatus


def _read_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def append_ticket(
    ticket: Ticket,
    *,
    runtime_root: Optional[Path] = None,
    scoped_mirror: bool = False,
) -> Ticket:
    """Append one ticket line to ``tickets.jsonl`` and refresh indexes."""
    path = tickets_jsonl_path(runtime_root=runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ticket.to_json_dict()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    if scoped_mirror and ticket.avenue_id:
        from trading_ai.intelligence.paths import scoped_tickets_root

        scoped = scoped_tickets_root(ticket.avenue_id, runtime_root=runtime_root) / "tickets.jsonl"
        scoped.parent.mkdir(parents=True, exist_ok=True)
        with scoped.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    rebuild_indexes(runtime_root=runtime_root)
    return ticket


def load_all_tickets(runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    return _read_jsonl(tickets_jsonl_path(runtime_root=runtime_root))


def load_ticket_by_id(ticket_id: str, runtime_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = tickets_jsonl_path(runtime_root=runtime_root)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("ticket_id") == ticket_id:
                return row
    return None


def rebuild_indexes(runtime_root: Optional[Path] = None) -> None:
    """Refresh ``open_tickets.json`` and ``ticket_summary.txt`` from the JSONL store."""
    all_rows = load_all_tickets(runtime_root=runtime_root)
    open_rows = [r for r in all_rows if r.get("status") in (TicketStatus.open.value, TicketStatus.investigating.value, TicketStatus.routed.value)]
    open_path = open_tickets_json_path(runtime_root=runtime_root)
    open_path.parent.mkdir(parents=True, exist_ok=True)
    open_path.write_text(json.dumps(open_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary_path = ticket_summary_txt_path(runtime_root=runtime_root)
    lines: List[str] = [
        "Ezras Trading AI — ticket summary (derived from append-only tickets.jsonl)",
        f"total_tickets: {len(all_rows)}",
        f"open_or_active: {len(open_rows)}",
        "",
    ]
    for r in sorted(open_rows, key=lambda x: x.get("created_at", ""), reverse=True)[:200]:
        tid = r.get("ticket_id", "")
        st = r.get("status", "")
        sev = r.get("severity", "")
        tt = r.get("ticket_type", "")
        summ = (r.get("human_plain_english_summary") or "")[:160]
        lines.append(f"{tid} | {st} | {sev} | {tt} | {summ}")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def iter_tickets_for_clustering(
    runtime_root: Optional[Path] = None,
    *,
    since_days: Optional[float] = None,
) -> Iterable[Dict[str, Any]]:
    """Yield ticket dicts (newest first) for daily clustering."""
    from datetime import datetime, timedelta, timezone

    rows = load_all_tickets(runtime_root=runtime_root)
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    for r in rows:
        if cutoff:
            try:
                ts = datetime.fromisoformat(str(r.get("created_at", "")).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except Exception:
                pass
        yield r


def count_by_category(
    tickets: Iterable[Dict[str, Any]],
) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in tickets:
        c = str(t.get("category") or t.get("ticket_type") or "unknown")
        out[c] = out.get(c, 0) + 1
    return out
