"""
Evidence-first truth engine.

Non-negotiable: no stage may PASS without concrete artifact evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class TruthStageResult:
    stage: str
    status: str  # "PASS" | "FAIL"
    evidence: List[str]
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "stage": self.stage,
            "status": self.status,
            "evidence": list(self.evidence),
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        j = json.loads(raw)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _exists(root: Path, rel: str) -> bool:
    try:
        return (root / rel).is_file()
    except Exception:
        return False


def truth_chain_for_post_trade(*, runtime_root: Path) -> List[TruthStageResult]:
    """
    Canonical chain for a completed trade cycle.

    Minimum: execution proof + pnl record. Risk is enforced via risk_state artifact.
    """
    root = Path(runtime_root).resolve()
    stages: List[TruthStageResult] = []

    exec_rel = "execution_proof/execution_proof.json"
    pnl_rel = "data/pnl/pnl_record.json"
    risk_rel = "data/risk/risk_state.json"

    if _exists(root, exec_rel):
        stages.append(TruthStageResult("execution", "PASS", [exec_rel]))
    else:
        stages.append(TruthStageResult("execution", "FAIL", [], f"missing:{exec_rel}"))

    if _exists(root, pnl_rel):
        # Ensure pnl has the required standardized structure.
        pnl = _read_json(root / pnl_rel)
        required = ("gross_pnl", "fees", "slippage", "net_pnl")
        missing = [k for k in required if k not in pnl]
        if missing:
            stages.append(
                TruthStageResult(
                    "pnl",
                    "FAIL",
                    [pnl_rel],
                    f"pnl_record_missing_fields:{','.join(missing)}",
                )
            )
        else:
            stages.append(TruthStageResult("pnl", "PASS", [pnl_rel]))
    else:
        stages.append(TruthStageResult("pnl", "FAIL", [], f"missing:{pnl_rel}"))

    if _exists(root, risk_rel):
        rs = _read_json(root / risk_rel)
        st = str(rs.get("status") or "").upper()
        if st in ("ACTIVE", "BLOCKED"):
            # Truth layer records evidence; it does not override enforcement.
            stages.append(TruthStageResult("risk", "PASS", [risk_rel]))
        else:
            stages.append(TruthStageResult("risk", "FAIL", [risk_rel], "risk_state_invalid"))
    else:
        stages.append(TruthStageResult("risk", "FAIL", [], f"missing:{risk_rel}"))

    return stages


def validate_truth_chain(
    chain: Iterable[TruthStageResult],
) -> Dict[str, Any]:
    rows = [c.to_dict() for c in chain]
    ok = all(r["status"] == "PASS" for r in rows)
    return {"ok": ok, "chain": rows}

