"""
Consistency engine: doctrine + agent alignment + tamper-evident audit chain + temporal samples.

Legacy plain JSONL path removed; governance events use ``governance/audit_chain.py`` only.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.governance import agent_alignment as _agents
from trading_ai.governance import system_doctrine as _sd
from trading_ai.governance.agent_alignment import AgentSpec
from trading_ai.governance.audit_chain import append_chained_event, chain_status, verify_audit_chain
from trading_ai.governance.operator_registry import registry_status, verify_doctrine_with_registry, verify_doctrine_registry_verdict
from trading_ai.governance.system_doctrine import DoctrineVerdict, compute_doctrine_sha256, verify_doctrine_integrity
from trading_ai.governance.temporal_consistency import build_temporal_summary, record_verdict_sample
from trading_ai.ops.automation_heartbeat import heartbeat_status
from trading_ai.security.encryption_at_rest import encryption_operational_status

logger = logging.getLogger(__name__)
_lock = threading.Lock()

BASELINE_FILENAME = "consistency_baseline.json"
TIMESERIES_FILENAME = "consistency_timeseries.jsonl"


def _baseline_path() -> Path:
    p = runtime_root() / "state" / BASELINE_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _timeseries_path() -> Path:
    p = runtime_root() / "logs" / TIMESERIES_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_audit_chained(record: Dict[str, Any]) -> None:
    try:
        append_chained_event({"channel": "consistency_engine", **record})
    except OSError as exc:
        logger.warning("audit chain append failed: %s", exc)


def _verdict_rank(v: str) -> int:
    order = {
        "ALIGNED": 0,
        "PARTIALLY_ALIGNED": 1,
        "DRIFTING": 2,
        "DOCTRINE_VIOLATION": 3,
        "HALT": 4,
    }
    return order.get(v, 2)


def _compute_impact_score(
    verdicts: Sequence[DoctrineVerdict],
    *,
    prior_baseline_distance: Optional[float] = None,
) -> float:
    if not verdicts:
        return 0.0
    worst = max(_verdict_rank(v.verdict) for v in verdicts)
    base = worst / 4.0
    sev_boost = sum(0.05 for v in verdicts if v.severity in ("CRITICAL", "HALT"))
    extra = 0.1 if prior_baseline_distance else 0.0
    return min(1.0, base + sev_boost + extra)


@dataclass
class ConsistencyBaseline:
    label: str
    created_at: datetime
    doctrine_sha256: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "doctrine_sha256": self.doctrine_sha256,
            "notes": self.notes,
        }


def load_baseline() -> Optional[ConsistencyBaseline]:
    p = _baseline_path()
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
        return ConsistencyBaseline(
            label=str(raw.get("label", "baseline")),
            created_at=ts,
            doctrine_sha256=str(raw.get("doctrine_sha256", "")),
            notes=str(raw.get("notes", "")),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_baseline(baseline: ConsistencyBaseline) -> None:
    p = _baseline_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(baseline.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(p)


def _append_timeseries(verdict: DoctrineVerdict) -> None:
    row = {
        "timestamp": verdict.timestamp.isoformat(),
        "verdict": verdict.verdict,
        "rule": verdict.rule_triggered,
        "severity": verdict.severity,
    }
    try:
        with _lock:
            with _timeseries_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        logger.warning("consistency timeseries append failed: %s", exc)


def _record_temporal(verdict: DoctrineVerdict) -> None:
    try:
        record_verdict_sample(verdict.verdict, rule_triggered=verdict.rule_triggered, source="consistency_engine")
    except OSError as exc:
        logger.warning("temporal record failed: %s", exc)


def evaluate_doctrine_alignment(
    *,
    change_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> DoctrineVerdict:
    v = _sd.evaluate_doctrine_alignment(
        change_type=change_type,
        payload=payload,
        context=context,
    )
    _append_audit_chained(
        {
            "kind": "doctrine_alignment",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "change_type": change_type,
            "result": v.to_dict(),
        }
    )
    _append_timeseries(v)
    _record_temporal(v)
    return v


def evaluate_agent_alignment(
    agent_specs: Sequence[AgentSpec],
    *,
    check_cross_agent: bool = True,
) -> List[DoctrineVerdict]:
    verdicts = _agents.evaluate_agent_alignment(agent_specs, check_cross_agent=check_cross_agent)
    _append_audit_chained(
        {
            "kind": "agent_alignment",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_ids": [s.agent_id for s in agent_specs],
            "results": [v.to_dict() for v in verdicts],
        }
    )
    for v in verdicts:
        _append_timeseries(v)
        _record_temporal(v)
    return verdicts


def evaluate_change_consistency(
    *,
    change_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
    agent_specs: Optional[Sequence[AgentSpec]] = None,
) -> Dict[str, Any]:
    doctrine_v = _sd.evaluate_doctrine_alignment(
        change_type=change_type,
        payload=payload,
        context=context,
    )
    collected: List[DoctrineVerdict] = [doctrine_v]
    agent_verdicts: List[DoctrineVerdict] = []
    if agent_specs:
        agent_verdicts = _agents.evaluate_agent_alignment(
            list(agent_specs),
            check_cross_agent=True,
        )
        collected.extend(agent_verdicts)

    for v in collected:
        _append_timeseries(v)
        _record_temporal(v)

    baseline = load_baseline()
    prior_dist: Optional[float] = None
    if baseline and baseline.doctrine_sha256 != compute_doctrine_sha256():
        prior_dist = 1.0

    impact = _compute_impact_score(collected, prior_baseline_distance=prior_dist)

    summary = {
        "change_type": change_type,
        "doctrine_verdict": doctrine_v.to_dict(),
        "agent_verdicts": [v.to_dict() for v in agent_verdicts],
        "consistency_delta_score": round(impact, 4),
        "baseline_label": baseline.label if baseline else None,
        "baseline_doctrine_match": (
            baseline.doctrine_sha256 == compute_doctrine_sha256() if baseline else None
        ),
    }
    _append_audit_chained(
        {
            "kind": "change_consistency",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }
    )
    return summary


def get_full_integrity_report() -> Dict[str, Any]:
    """Combined integrity: module hash, registry, audit chain."""
    mod = verify_doctrine_integrity()
    reg = verify_doctrine_with_registry()
    chain_v = verify_audit_chain()
    reg_verdict = verify_doctrine_registry_verdict()

    overall_ok = mod.verdict != "HALT" and chain_v.ok and reg_verdict.verdict != "HALT"

    return {
        "overall_ok": overall_ok,
        "module_integrity": mod.to_dict(),
        "operator_registry": reg,
        "audit_chain": {
            "ok": chain_v.ok,
            "records_verified": chain_v.records_verified,
            "first_bad_line": chain_v.first_bad_line,
            "detail": chain_v.detail,
            "tamper_evident_failure": not chain_v.ok,
        },
        "registry_verdict": reg_verdict.to_dict(),
    }


def get_consistency_status() -> Dict[str, Any]:
    integrity = verify_doctrine_integrity()
    baseline = load_baseline()
    reg_status = registry_status()
    rep = get_full_integrity_report()
    return {
        "doctrine": {
            "integrity_verdict": integrity.to_dict(),
            "sha256": compute_doctrine_sha256(),
        },
        "operator_registry": reg_status,
        "audit_chain": chain_status(),
        "full_integrity": rep,
        "encryption_at_rest": encryption_operational_status(),
        "baseline": baseline.to_dict() if baseline else None,
        "timeseries_log_path": str(_timeseries_path()),
        "runtime_root": str(runtime_root()),
        "temporal": build_temporal_summary(),
        "automation_heartbeat": heartbeat_status(),
    }


def temporal_consistency_summary(days: int = 30) -> Dict[str, Any]:
    """Backward-compatible shallow read of timeseries JSONL + link to deep temporal state."""
    p = _timeseries_path()
    deep = build_temporal_summary()
    if not p.is_file():
        return {"ok": True, "entries": [], "note": "no timeseries yet", "deep": deep}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ts_s = row.get("timestamp")
            if not ts_s:
                continue
            ts = datetime.fromisoformat(str(ts_s).replace("Z", "+00:00"))
            if ts >= cutoff:
                entries.append(row)
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "timeseries_read_failed", "deep": deep}
    drift_hits = sum(1 for e in entries if e.get("verdict") in ("DRIFTING", "DOCTRINE_VIOLATION", "HALT"))
    return {
        "ok": True,
        "window_days": days,
        "entry_count": len(entries),
        "drift_or_violation_events": drift_hits,
        "recent": entries[-20:],
        "deep": deep,
    }
