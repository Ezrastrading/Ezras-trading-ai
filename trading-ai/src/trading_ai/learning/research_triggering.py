"""Research review artifacts — placeholders are honest; no external research APIs here."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def write_daily_research_review(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    rv = root / "data" / "review"
    rv.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "advisory_scaffold",
        "topics": [
            "venue liquidity vs observed slippage",
            "regime tags vs realized PnL by edge",
        ],
        "status": "proposal_only_not_executed",
        "external_research_connected": False,
        "note": "Connect external research feeds later; this file is a structured placeholder.",
    }
    (rv / "daily_research_review.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    lines = [
        "DAILY RESEARCH REVIEW (placeholder)",
        f"generated_at: {payload['generated_at_utc']}",
        "classification: advisory_scaffold — not live research execution.",
        "",
        "topics:",
        *[f"  - {t}" for t in payload["topics"]],
    ]
    (rv / "daily_research_review.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
