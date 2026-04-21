"""One canonical writer id per truth domain — bots propose; only designated writers finalize."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.global_layer.orchestration_paths import bot_auto_promotion_truth_path, capital_governor_readiness_truth_path


class TruthDomain(str, Enum):
    EXECUTION = "execution"
    PROMOTION = "promotion"
    CAPITAL = "capital"
    REVIEW = "review"


# Process-level writer identities (not bot_ids). Orchestration / CEO pipelines use these.
CANONICAL_WRITER_IDS: Dict[TruthDomain, str] = {
    TruthDomain.EXECUTION: "system_execution_truth_writer",
    TruthDomain.PROMOTION: "system_promotion_truth_writer",
    TruthDomain.CAPITAL: "system_capital_truth_writer",
    TruthDomain.REVIEW: "system_review_truth_writer",
}


def is_canonical_writer(domain: TruthDomain, writer_id: str) -> bool:
    return str(writer_id).strip() == CANONICAL_WRITER_IDS.get(domain, "")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_dir(primary: Path) -> Path:
    return primary.parent / f"{primary.stem}_history"


def _version_and_rollback(primary: Path, payload: Dict[str, Any], writer_id: str, domain: str) -> Dict[str, Any]:
    ver = 1
    prev_snapshot: Optional[str] = None
    if primary.is_file():
        try:
            old = json.loads(primary.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                ver = int(old.get("artifact_version") or 1) + 1
                prev_snapshot = str(old.get("content_hash") or "")[:16] or None
        except (OSError, json.JSONDecodeError):
            ver = 2
    hist = _history_dir(primary)
    hist.mkdir(parents=True, exist_ok=True)
    if primary.is_file():
        dest = hist / f"v{ver - 1}_{primary.name}"
        try:
            shutil.copy2(primary, dest)
        except OSError:
            pass
    body = {
        "truth_version": f"{domain}_truth_wrapped_v1",
        "artifact_version": ver,
        "domain": domain,
        "writer_id": writer_id,
        "written_at": _iso(),
        "previous_snapshot_hint": prev_snapshot,
        "rollback_path": str(hist / f"v{ver - 1}_{primary.name}") if ver > 1 else None,
        "payload": payload,
    }
    return body


def _write_wrapped(primary: Path, wrapped: Dict[str, Any]) -> None:
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text(json.dumps(wrapped, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def finalize_promotion_cycle_truth(
    payload: Dict[str, Any],
    *,
    writer_id: str,
) -> Dict[str, Any]:
    if not is_canonical_writer(TruthDomain.PROMOTION, writer_id):
        raise PermissionError(f"not_canonical_promotion_writer:{writer_id}")
    p = bot_auto_promotion_truth_path()
    wrapped = _version_and_rollback(p, payload, writer_id, "promotion")
    _write_wrapped(p, wrapped)
    return wrapped


def finalize_capital_readiness_truth(
    payload: Dict[str, Any],
    *,
    writer_id: str,
) -> Dict[str, Any]:
    if not is_canonical_writer(TruthDomain.CAPITAL, writer_id):
        raise PermissionError(f"not_canonical_capital_writer:{writer_id}")
    p = capital_governor_readiness_truth_path()
    wrapped = _version_and_rollback(p, payload, writer_id, "capital")
    _write_wrapped(p, wrapped)
    return wrapped


def write_execution_truth_summary(
    payload: Dict[str, Any],
    *,
    writer_id: str,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Pointers/summary for execution domain — does not replace venue proof files."""
    if not is_canonical_writer(TruthDomain.EXECUTION, writer_id):
        raise PermissionError(f"not_canonical_execution_writer:{writer_id}")
    from trading_ai.global_layer.orchestration_paths import orchestration_root

    p = path or (orchestration_root() / "execution_truth_summary.json")
    wrapped = _version_and_rollback(p, payload, writer_id, "execution")
    _write_wrapped(p, wrapped)
    return wrapped
