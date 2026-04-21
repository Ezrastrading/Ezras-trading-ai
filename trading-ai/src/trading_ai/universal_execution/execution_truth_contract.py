"""
Canonical execution truth stages — no later stage may be True without prerequisite stages.

Avenue- and gate-agnostic; venue specifics live in adapters and ``avenue_specific_json`` on records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional


class ExecutionTruthStage(IntEnum):
    STAGE_0_CANDIDATE_SELECTED = 0
    STAGE_1_PRETRADE_GUARDS_PASSED = 1
    STAGE_2_ENTRY_ORDER_SUBMITTED = 2
    STAGE_3_ENTRY_FILL_CONFIRMED = 3
    STAGE_4_EXIT_ORDER_SUBMITTED = 4
    STAGE_5_EXIT_FILL_CONFIRMED = 5
    STAGE_6_PNL_VERIFIED = 6
    STAGE_7_LOCAL_DATA_WRITTEN = 7
    STAGE_8_REMOTE_DATA_WRITTEN = 8
    STAGE_9_GOVERNANCE_LOGGED = 9
    STAGE_10_REVIEW_ARTIFACTS_UPDATED = 10
    STAGE_11_READY_FOR_NEXT_CYCLE = 11


@dataclass
class StageStatus:
    ok: bool
    ts: Optional[str] = None
    avenue_id: str = ""
    gate_id: str = ""
    strategy_id: str = ""
    route: str = ""
    execution_profile: str = ""
    blocking_reason: Optional[str] = None
    proof_source: str = ""
    proof_kind: str = ""
    trade_id: Optional[str] = None
    previous_stage_dependency: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ok": self.ok,
            "timestamp": self.ts,
            "avenue_id": self.avenue_id,
            "gate_id": self.gate_id,
            "strategy_id": self.strategy_id,
            "route": self.route,
            "execution_profile": self.execution_profile,
            "blocking_reason": self.blocking_reason,
            "proof_source": self.proof_source,
            "proof_kind": self.proof_kind,
            "trade_id": self.trade_id,
            "previous_stage_dependency": self.previous_stage_dependency,
        }
        return {k: v for k, v in d.items() if v is not None or k == "ok"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionTruthContract:
    """Full stage map — default all False until proven."""

    stages: Dict[int, StageStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for i in range(12):
            if i not in self.stages:
                self.stages[i] = StageStatus(ok=False, previous_stage_dependency=i - 1 if i > 0 else None)

    def set_stage(
        self,
        stage: ExecutionTruthStage,
        *,
        ok: bool,
        **kwargs: Any,
    ) -> None:
        prev = int(stage) - 1
        if prev >= 0:
            p = self.stages.get(prev)
            if p and not p.ok and ok:
                raise ValueError(
                    f"Cannot mark stage {stage.name} true while prerequisite stage {prev} is false "
                    f"(honesty invariant)."
                )
        st = StageStatus(
            ok=ok,
            ts=_now_iso(),
            previous_stage_dependency=prev if prev >= 0 else None,
            **kwargs,
        )
        self.stages[int(stage)] = st

    def prerequisite_ok(self, stage: ExecutionTruthStage) -> bool:
        if int(stage) == 0:
            return True
        p = self.stages.get(int(stage) - 1)
        return bool(p and p.ok)

    def to_dict(self) -> Dict[str, Any]:
        return {ExecutionTruthStage(k).name: v.to_dict() for k, v in sorted(self.stages.items())}
