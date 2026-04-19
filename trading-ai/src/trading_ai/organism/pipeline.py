"""Post-trade ACCO hook — delegates registry / promotion to :mod:`trading_ai.edge`; adds operating mode + meta."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.validation import apply_evaluation, promote_testing_if_candidate
from trading_ai.edge.failsafe import combined_failsafe
from trading_ai.nte.databank.local_trade_store import load_all_trade_events
from trading_ai.organism.meta_learning import update_meta_from_closed_trade
from trading_ai.organism.operating_modes import env_override_mode, resolve_operating_mode, save_mode_state
from trading_ai.organism.trade_truth import validate_trade_truth

logger = logging.getLogger(__name__)


def _rolling_dd_expectancy(events: List[Mapping[str, Any]]) -> Dict[str, float]:
    tail = list(events)[-60:]
    pnls = [float(e.get("net_pnl") or 0.0) for e in tail if isinstance(e, dict)]
    n = len(pnls)
    if n < 2:
        return {"expectancy": 0.0, "dd_ratio": 0.0}
    exp = sum(pnls) / n
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    net = sum(pnls)
    dd_r = min(1.0, mdd / max(abs(net), 1e-9)) if net != 0 else 0.0
    return {"expectancy": exp, "dd_ratio": dd_r}


class OrganismClosedTradeHook:
    """Run after a trade is validated and stored — edge promotion + safety posture."""

    @staticmethod
    def pre_validate(raw: Mapping[str, Any]) -> tuple[bool, List[str]]:
        errs: List[str] = []
        ok, msg = validate_trade_truth(raw)
        if not ok and msg:
            errs.append(msg)
        return ok, errs

    @staticmethod
    def after_closed_trade(
        merged: Dict[str, Any],
        *,
        stages: Optional[Mapping[str, Any]] = None,
        pipeline_partial: bool = False,
    ) -> Dict[str, Any]:
        stages = dict(stages or {})
        edge_id = str(merged.get("edge_id") or "").strip()

        registry = EdgeRegistry()
        events = load_all_trade_events()

        edge_report: Dict[str, Any] = {}
        if edge_id:
            e = registry.get(edge_id)
            if e is None:
                logger.warning("acco: trade references unknown edge_id=%s — register via research materialization", edge_id)
            else:
                promote_testing_if_candidate(registry, edge_id)
                changed, rep = apply_evaluation(registry, events, edge_id)
                edge_report = {"changed": changed, "evaluation": rep}

            update_meta_from_closed_trade(merged, research_source=merged.get("research_source"))

        fs = combined_failsafe(events, edge_id=edge_id or None)
        halt = bool(fs.get("halt_trading"))
        rm = _rolling_dd_expectancy(events)
        ov = env_override_mode()
        if ov is None:
            mode, why = resolve_operating_mode(
                rolling_expectancy=rm["expectancy"],
                recent_drawdown_ratio=rm["dd_ratio"],
                pipeline_ok=not pipeline_partial and bool(stages.get("validated", True)),
            )
        else:
            mode, why = ov, "env_override"
        save_mode_state(
            {
                "mode": mode.value,
                "reason": why,
                "rolling_expectancy": rm["expectancy"],
                "failsafe_halt": halt,
            }
        )

        return {
            "edge_hook": edge_report,
            "failsafe": fs,
            "operating_mode": mode.value,
            "trading_halted": halt,
        }
