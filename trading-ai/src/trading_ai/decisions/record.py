from __future__ import annotations

from datetime import datetime
from typing import Optional

from trading_ai.models.schemas import DecisionRecord
from trading_ai.storage.store import Store


def record_decision(
    store: Store,
    *,
    market_id: str,
    brief_created_at: str,
    action: str,
    notes: Optional[str] = None,
) -> None:
    """Persist a human decision tied to a brief. CLI and future UIs call this."""
    brief_ts = datetime.fromisoformat(brief_created_at.replace("Z", "+00:00"))
    store.log_decision(
        DecisionRecord(
            market_id=market_id,
            brief_created_at=brief_ts,
            action=action,
            notes=notes,
        )
    )
