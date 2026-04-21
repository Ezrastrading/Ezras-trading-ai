"""Org hierarchy — CEO → avenue directors → gate specialists → role support bots."""

from __future__ import annotations

from typing import Any, Dict, List


def default_hierarchy() -> Dict[str, Any]:
    return {
        "truth_version": "bot_org_chart_v1",
        "root": "CEO",
        "children": [
            {
                "role": "AvenueDirector",
                "avenue": "A",
                "children": [
                    {"role": "GateSpecialist", "gate": "gate_a"},
                    {"role": "GateSpecialist", "gate": "gate_b"},
                ],
            },
            {
                "role": "AvenueDirector",
                "avenue": "B",
                "children": [{"role": "GateSpecialist", "gate": "gate_b"}],
            },
        ],
        "support": [
            {"role": "SCANNER", "reports_to": "AvenueDirector"},
            {"role": "DECISION", "reports_to": "AvenueDirector"},
            {"role": "EXECUTION", "reports_to": "Risk", "note": "single execution authority per avenue/gate"},
            {"role": "RISK", "reports_to": "CEO"},
            {"role": "LEARNING", "reports_to": "CEO"},
        ],
        "escalation_default": ["RISK", "CEO"],
    }


def reporting_chain(bot_role: str) -> List[str]:
    if bot_role in ("RISK", "LEARNING"):
        return ["CEO"]
    if bot_role == "EXECUTION":
        return ["RISK", "CEO"]
    return ["AvenueDirector", "CEO"]
