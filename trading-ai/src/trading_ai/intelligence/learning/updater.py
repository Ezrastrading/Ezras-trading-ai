"""Additive, versioned learning file updates — Part 7 rules."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from trading_ai.intelligence.learning.instrument_domains import empty_instrument_facets
from trading_ai.intelligence.paths import (
    learning_change_log_jsonl_path,
    learning_domains_dir,
    learning_snapshots_dir,
)

UpdateType = Literal["additive", "revised", "downgraded", "archived"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_domain_document(domain_id: str) -> Dict[str, Any]:
    return {
        "domain": domain_id,
        "version": 1,
        "updated_at": _utc_now(),
        "what_the_system_currently_knows": "",
        "confidence": 0.0,
        "proven_patterns": [],
        "unproven_hypotheses": [],
        "common_failure_modes": [],
        "venue_specific_differences": {},
        "gate_specific_differences": {},
        "good_environments": [],
        "bad_environments": [],
        "promising_strategies": [],
        "dangerous_strategies": [],
        "latency_sensitivity": "",
        "liquidity_sensitivity": "",
        "risk_notes": [],
        "next_research_questions": [],
        "source_of_truth_references": [],
        "instrument_facets": empty_instrument_facets(),
        "history": [],
    }


def ensure_domain_files(domain_id: str, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Create domain JSON/TXT if missing — honest empty shells."""
    ddir = learning_domains_dir(runtime_root=runtime_root)
    jp = ddir / f"{domain_id}.json"
    tp = ddir / f"{domain_id}.txt"
    if not jp.exists():
        doc = default_domain_document(domain_id)
        jp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    doc = json.loads(jp.read_text(encoding="utf-8"))
    if not tp.exists() or tp.stat().st_size == 0:
        tp.write_text(
            "\n".join(
                [
                    f"Domain: {domain_id}",
                    "This file mirrors the JSON — human-readable summary.",
                    doc.get("what_the_system_currently_knows") or "(no claims yet)",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return doc


def _snapshot_file(domain_id: str, version: int, runtime_root: Optional[Path] = None) -> Path:
    sdir = learning_snapshots_dir(runtime_root=runtime_root)
    return sdir / f"{domain_id}_v{version}.json"


def maybe_update_domain(
    domain_id: str,
    *,
    reason: str,
    supporting_ticket_ids: List[str],
    confidence: float,
    update_type: UpdateType,
    source_scope: str,
    patch: Dict[str, Any],
    runtime_root: Optional[Path] = None,
    min_confidence: float = 0.55,
) -> Dict[str, Any]:
    """
    Apply an additive/revised patch if evidence + confidence threshold met.
    Never silently deletes prior content — revised wraps prior into history.
    """
    if confidence < min_confidence and update_type != "archived":
        return {"ok": False, "reason": "below_confidence_threshold", "min_confidence": min_confidence}
    if not supporting_ticket_ids and update_type not in ("archived",):
        return {"ok": False, "reason": "missing_ticket_refs"}

    doc = ensure_domain_files(domain_id, runtime_root=runtime_root)
    prev_snapshot_path = _snapshot_file(domain_id, int(doc.get("version", 1)), runtime_root=runtime_root)
    prev_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(learning_domains_dir(runtime_root=runtime_root) / f"{domain_id}.json", prev_snapshot_path)

    hist_entry = {
        "at": _utc_now(),
        "prior_version": doc.get("version", 1),
        "update_type": update_type,
        "reason": reason,
        "supporting_ticket_ids": supporting_ticket_ids,
        "confidence": confidence,
        "source_scope": source_scope,
        "patch_keys": list(patch.keys()),
    }
    doc.setdefault("history", []).append(hist_entry)

    if update_type == "additive":
        for k, v in patch.items():
            if k in ("proven_patterns", "unproven_hypotheses", "risk_notes", "next_research_questions"):
                doc[k] = list(doc.get(k) or []) + (v if isinstance(v, list) else [v])
            elif k in ("venue_specific_differences", "gate_specific_differences"):
                base = doc.get(k) or {}
                if isinstance(base, dict) and isinstance(v, dict):
                    merged = {**base, **v}
                    doc[k] = merged
            elif k == "instrument_facets" and isinstance(v, dict):
                base = doc.get("instrument_facets") or {}
                doc["instrument_facets"] = {**base, **v}
            else:
                # String / scalar additive: append with delimiter for transparency
                if isinstance(doc.get(k), str) and isinstance(v, str):
                    doc[k] = (doc[k] + "\n" + v).strip()
                else:
                    doc[k] = v
    elif update_type in ("revised", "downgraded"):
        doc.update(patch)
        doc["confidence"] = min(float(doc.get("confidence", 0)), confidence)
    elif update_type == "archived":
        doc["archived"] = True
        doc.update(patch)

    doc["version"] = int(doc.get("version", 1)) + 1
    doc["updated_at"] = _utc_now()

    jp = learning_domains_dir(runtime_root=runtime_root) / f"{domain_id}.json"
    jp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tp = learning_domains_dir(runtime_root=runtime_root) / f"{domain_id}.txt"
    tp.write_text(
        "\n".join(
            [
                f"Domain: {domain_id} (v{doc['version']})",
                doc.get("what_the_system_currently_knows") or "",
                "",
                "Proven patterns:",
                "\n".join(f"- {p}" for p in (doc.get("proven_patterns") or [])[:40]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    log_line = {
        "updated_at": doc["updated_at"],
        "domain_id": domain_id,
        "reason": reason,
        "supporting_ticket_ids": supporting_ticket_ids,
        "confidence": confidence,
        "update_type": update_type,
        "source_scope": source_scope,
        "new_version": doc["version"],
    }
    logp = learning_change_log_jsonl_path(runtime_root=runtime_root)
    logp.parent.mkdir(parents=True, exist_ok=True)
    with logp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_line, ensure_ascii=False) + "\n")

    return {"ok": True, "domain_id": domain_id, "version": doc["version"]}
