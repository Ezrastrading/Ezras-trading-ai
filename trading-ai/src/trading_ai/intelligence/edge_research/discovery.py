"""Discover candidate research records from tickets, ledgers, and proving artifacts (scoped, honest)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.edge_research.artifacts import PROVING_ARTIFACT_PATHS, proving_catalog_snapshot
from trading_ai.intelligence.edge_research.models import (
    InstrumentResearchRecord,
    ResearchRecordCore,
    ResearchStatus,
    StrategyResearchRecord,
)
from trading_ai.intelligence.edge_research.registry import merge_records
from trading_ai.intelligence.paths import tickets_jsonl_path
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _read_jsonl_tail(path: Path, max_lines: int = 500) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                out.append(d)
        except json.JSONDecodeError:
            continue
    return out


def ticket_to_candidate_record(t: Dict[str, Any]) -> Optional[ResearchRecordCore]:
    """Map ticket to a scoped strategy/edge research row — default hypothesis."""
    tid = str(t.get("ticket_id") or "")
    if not tid:
        return None
    avenue = str(t.get("avenue_id") or "")
    gate = str(t.get("gate_id") or "")
    tt = str(t.get("ticket_type") or "")
    cat = str(t.get("category") or "")
    summary = str(t.get("human_plain_english_summary") or t.get("machine_summary") or "")
    ev = list(t.get("evidence_refs") or [])
    conf = float(t.get("confidence") or 0.0)

    st = ResearchStatus.hypothesis
    if "edge_opportunity" in tt or "edge" in cat.lower():
        edge_name = f"ticket_derived:{tid}"
    else:
        edge_name = ""

    rec = StrategyResearchRecord(
        record_id=f"er_ticket_{tid}",
        avenue_id=avenue,
        gate_id=gate,
        venue=str(t.get("venue") or ""),
        market_type=str(t.get("market_type") or ""),
        instrument_type=str(t.get("instrument_type") or ""),
        product_id=str(t.get("product_id") or ""),
        market_id=str(t.get("market_id") or ""),
        contract_id=str(t.get("contract_id") or ""),
        strategy_name=cat or tt,
        edge_name=edge_name,
        current_status=st,
        confidence=min(0.99, max(0.0, conf)),
        evidence_refs=ev,
        supporting_ticket_ids=[tid],
        supporting_artifact_paths=[],
        conditions_where_it_works="",
        conditions_where_it_fails="",
        key_risks=str(t.get("likely_root_cause") or ""),
        edge_mechanism="",
        execution_requirements="",
        liquidity_requirements="",
        latency_requirements="",
        venue_specific_notes="",
        comparison_summary="",
        recommended_next_test=str(t.get("recommended_action") or ""),
        operator_plain_english_summary=summary or f"Derived from ticket {tid}",
        extra={"source": "tickets.jsonl", "ticket_type": tt},
    )
    return rec


def discover_from_tickets(*, runtime_root: Optional[Path] = None, max_lines: int = 500) -> List[Dict[str, Any]]:
    p = tickets_jsonl_path(runtime_root=runtime_root)
    discovered: List[Dict[str, Any]] = []
    for t in _read_jsonl_tail(p, max_lines=max_lines):
        r = ticket_to_candidate_record(t)
        if r:
            discovered.append(r.to_json_dict())
    return discovered


def discover_proving_catalog_record(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Single registry row describing which proving artifacts exist (not claiming edge)."""
    snap = proving_catalog_snapshot(runtime_root=runtime_root)
    present = [e["path"] for e in snap.get("entries") or [] if e.get("exists")]
    rid = "er_proving_layer_catalog"
    body = ResearchRecordCore(
        record_id=rid,
        record_kind="research_core",
        avenue_id="",
        gate_id="",
        strategy_name="proving_layer_catalog",
        edge_name="n/a",
        current_status=ResearchStatus.under_research,
        confidence=1.0 if present else 0.2,
        supporting_artifact_paths=sorted(present),
        evidence_refs=[f"catalog:{p}" for p in PROVING_ARTIFACT_PATHS],
        operator_plain_english_summary="Catalog of which proving artifacts exist under data/control — presence does not prove profitability.",
        extra=snap,
    )
    return body.to_json_dict()


def discover_from_trade_ledger_stub(*, runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Placeholder for future ledger JSONL — returns empty list if no file."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    candidates = [
        root / "data" / "control" / "trade_ledger.jsonl",
        root / "data" / "ledger" / "trades.jsonl",
    ]
    for p in candidates:
        if p.is_file():
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            return [
                InstrumentResearchRecord(
                    record_id="er_ledger_stub_scan",
                    avenue_id="",
                    gate_id="",
                    strategy_name="trade_ledger_scan",
                    current_status=ResearchStatus.under_research,
                    supporting_artifact_paths=[rel],
                    operator_plain_english_summary=f"Ledger file present at {p.name} — extend parsers to extract patterns.",
                ).to_json_dict()
            ]
    return []


def discover_from_summary_artifacts(*, runtime_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Hook daily/weekly summaries if present."""
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    out: List[Dict[str, Any]] = []
    for rel in (
        "data/reports/daily_trading_summary.json",
        "data/control/daily_trading_summary.json",
    ):
        raw = ad.read_json(rel)
        if raw:
            out.append(
                ResearchRecordCore(
                    record_id="er_summary_artifact_latest",
                    record_kind="domain",
                    strategy_name="summary_ingest",
                    current_status=ResearchStatus.hypothesis,
                    supporting_artifact_paths=[rel],
                    operator_plain_english_summary="Summary artifact detected — review for regime / slippage patterns manually or extend NLP.",
                    extra={"summary_keys": list(raw.keys())[:40]},
                ).to_json_dict()
            )
            break
    return out


def run_discovery(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """Run all discovery passes and merge into registry."""
    rows: List[Dict[str, Any]] = []
    rows.extend(discover_from_tickets(runtime_root=runtime_root))
    rows.append(discover_proving_catalog_record(runtime_root=runtime_root))
    rows.extend(discover_from_trade_ledger_stub(runtime_root=runtime_root))
    rows.extend(discover_from_summary_artifacts(runtime_root=runtime_root))
    reg = merge_records(rows, runtime_root=runtime_root)
    return {
        "status": "ok",
        "merged_count": len(rows),
        "registry_record_count": len(reg.get("records") or []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
