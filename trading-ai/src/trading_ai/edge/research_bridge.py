"""Create EdgeRegistry rows from strategy research JSONL (file-based, no import cycles)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.edge.models import EdgeRecord, EdgeStatus, classify_edge_type
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


def strategy_research_log_default_path() -> Path:
    return ezras_runtime_root() / "strategy_research" / "research_log.jsonl"


def _hypothesis_hash(text: str) -> str:
    raw = (text or "")[:2000].encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def _infer_avenue(hypothesis: str) -> str:
    h = (hypothesis or "").lower()
    if "kalshi" in h or "prediction market" in h:
        return "kalshi"
    if "option" in h or "spy" in h or "iv " in h:
        return "options"
    return "coinbase"


def materialize_from_research_log_path(
    path: Optional[Path] = None,
    *,
    lines_limit: int = 8,
    registry: Optional[EdgeRegistry] = None,
) -> Dict[str, Any]:
    """
    Parse last ``lines_limit`` JSONL records from research log; create **candidate** edges.

    Does not enable execution scaling — status remains ``candidate`` until promoted to ``testing``.
    """
    log_path = path or strategy_research_log_default_path()
    reg = registry or EdgeRegistry()
    created = 0
    skipped = 0
    if not log_path.is_file():
        return {"ok": False, "reason": "missing_log", "path": str(log_path), "created": 0}

    lines = log_path.read_text(encoding="utf-8").splitlines()
    tail = [ln for ln in lines[-lines_limit:] if ln.strip()]

    existing_hashes = set()
    for e in reg.list_edges():
        existing_hashes.add(_hypothesis_hash(e.hypothesis_text))

    for line in tail:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        hyp = str(row.get("hypothesis") or "").strip()
        if not hyp:
            skipped += 1
            continue
        hh = _hypothesis_hash(hyp)
        if hh in existing_hashes:
            skipped += 1
            continue

        avenue = _infer_avenue(hyp)
        etype = classify_edge_type(hyp)
        conf_map = {"LOW": 0.2, "MEDIUM": 0.45, "HIGH": 0.65}
        conf = conf_map.get(str(row.get("confidence") or "LOW").upper(), 0.2)

        edge = EdgeRecord(
            edge_id=EdgeRegistry.new_edge_id(),
            avenue=avenue,
            edge_type=etype,
            hypothesis_text=hyp[:12000],
            required_conditions={
                "source": "strategy_research",
                "market_context": row.get("market_context"),
                "research_confidence": row.get("confidence"),
            },
            status=EdgeStatus.CANDIDATE.value,
            confidence=conf,
            source_research_ts=str(row.get("timestamp") or ""),
            source=str(row.get("source") or "research_log"),
        )
        # Optional: link NTE strategy name if mentioned in text
        m = re.search(r"\b(mean_reversion|continuation_pullback)\b", hyp, re.I)
        if m:
            edge.linked_strategy_id = m.group(1).lower()

        reg.upsert(edge)
        existing_hashes.add(hh)
        created += 1

    return {"ok": True, "path": str(log_path), "created": created, "skipped": skipped}
