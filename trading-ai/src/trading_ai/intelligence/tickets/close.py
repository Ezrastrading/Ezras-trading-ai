"""Ticket lifecycle: resolve, archive — indexes rebuilt via store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from trading_ai.intelligence.tickets.models import Ticket, TicketStatus
from trading_ai.intelligence.tickets.store import load_all_tickets, tickets_jsonl_path
from trading_ai.intelligence.tickets.store import rebuild_indexes


def _rewrite_store(rows: list, runtime_root: Optional[Path] = None) -> None:
    """Rewrite JSONL (not append) — use only for status updates with small corpora."""
    path = tickets_jsonl_path(runtime_root=runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def update_ticket_status(
    ticket_id: str,
    new_status: TicketStatus,
    *,
    runtime_root: Optional[Path] = None,
    note: str = "",
) -> bool:
    """Rewrite line for ``ticket_id`` with new status. Returns False if not found."""
    rows = load_all_tickets(runtime_root=runtime_root)
    found = False
    for row in rows:
        if row.get("ticket_id") == ticket_id:
            row["status"] = new_status.value
            if note:
                row.setdefault("extra", {})
                if isinstance(row["extra"], dict):
                    row["extra"]["close_note"] = note
            found = True
            break
    if not found:
        return False
    _rewrite_store(rows, runtime_root=runtime_root)
    rebuild_indexes(runtime_root=runtime_root)
    return True


def close_ticket(
    ticket_id: str,
    *,
    runtime_root: Optional[Path] = None,
    resolution_note: str = "",
) -> bool:
    return update_ticket_status(
        ticket_id,
        TicketStatus.resolved,
        runtime_root=runtime_root,
        note=resolution_note,
    )


def archive_ticket(
    ticket_id: str,
    *,
    runtime_root: Optional[Path] = None,
) -> bool:
    return update_ticket_status(ticket_id, TicketStatus.archived, runtime_root=runtime_root)


def materialize_ticket_from_dict(data: dict) -> Ticket:
    """Parse stored dict back to Ticket model."""
    return Ticket.model_validate(data)
