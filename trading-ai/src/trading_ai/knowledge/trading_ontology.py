"""
Canonical trading concepts as structured schemas (machine-readable).

Not a strategy layer — definitions and relationships only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LiquidityTier(str, Enum):
    DEEP = "deep"
    MODERATE = "moderate"
    THIN = "thin"


@dataclass
class OntologyTerm:
    """Single glossary entry with optional relations."""

    id: str
    definition: str
    related_ids: List[str] = field(default_factory=list)


@dataclass
class PositionConcept:
    """Abstract notion of exposure."""

    instrument_id: str
    venue: str
    quantity: float
    side: str  # long|short|flat


@dataclass
class OrderbookLevelConcept:
    bid: float
    ask: float
    spread: float


TRADING_ONTOLOGY: Dict[str, OntologyTerm] = {
    "position": OntologyTerm(
        id="position",
        definition="Net exposure to an instrument's price or settlement outcome.",
        related_ids=["entry", "exit", "pnl", "risk"],
    ),
    "entry": OntologyTerm(
        id="entry",
        definition="Point at which risk is opened (fill or working order acceptance).",
        related_ids=["fill", "exit", "edge"],
    ),
    "exit": OntologyTerm(
        id="exit",
        definition="Point at which risk is closed or settled.",
        related_ids=["fill", "pnl", "expectancy"],
    ),
    "fill": OntologyTerm(
        id="fill",
        definition="Executed trade at a price and size.",
        related_ids=["slippage", "fee", "latency"],
    ),
    "bid": OntologyTerm(
        id="bid",
        definition="Best price buyers are willing to pay.",
        related_ids=["ask", "spread"],
    ),
    "ask": OntologyTerm(
        id="ask",
        definition="Best price sellers will accept.",
        related_ids=["bid", "spread"],
    ),
    "spread": OntologyTerm(
        id="spread",
        definition="Ask minus bid — friction and information cost.",
        related_ids=["liquidity", "slippage"],
    ),
    "slippage": OntologyTerm(
        id="slippage",
        definition="Difference between expected and realized fill vs reference.",
        related_ids=["fill", "latency", "liquidity"],
    ),
    "fee": OntologyTerm(
        id="fee",
        definition="Explicit costs charged by venue or chain.",
        related_ids=["pnl", "expectancy"],
    ),
    "pnl": OntologyTerm(
        id="pnl",
        definition="Profit or loss after costs for a trade or period.",
        related_ids=["expectancy", "drawdown"],
    ),
    "expectancy": OntologyTerm(
        id="expectancy",
        definition="Average PnL per trade over a defined sample.",
        related_ids=["edge", "variance", "pnl"],
    ),
    "drawdown": OntologyTerm(
        id="drawdown",
        definition="Peak-to-trough equity decline over a window.",
        related_ids=["risk", "variance"],
    ),
    "variance": OntologyTerm(
        id="variance",
        definition="Dispersion of outcomes around mean PnL.",
        related_ids=["expectancy", "risk"],
    ),
    "edge": OntologyTerm(
        id="edge",
        definition="Durable positive expectancy after fees when conditions hold.",
        related_ids=["expectancy", "fee", "discipline"],
    ),
    "risk": OntologyTerm(
        id="risk",
        definition="Exposure to adverse movement, default, or operational failure.",
        related_ids=["leverage", "drawdown", "liquidity"],
    ),
    "leverage": OntologyTerm(
        id="leverage",
        definition="Notional exposure relative to capital.",
        related_ids=["risk", "drawdown"],
    ),
    "liquidity": OntologyTerm(
        id="liquidity",
        definition="Ability to trade size without large price impact.",
        related_ids=["spread", "slippage"],
    ),
    "latency": OntologyTerm(
        id="latency",
        definition="Time from decision to actionable confirmation.",
        related_ids=["fill", "slippage"],
    ),
    "discipline": OntologyTerm(
        id="discipline",
        definition="Consistency in following process and risk limits.",
        related_ids=["edge", "risk"],
    ),
}


def all_term_ids() -> List[str]:
    return sorted(TRADING_ONTOLOGY.keys())


def validate_ontology_internal() -> List[str]:
    """Return inconsistency messages (empty if OK)."""
    errs: List[str] = []
    for tid, term in TRADING_ONTOLOGY.items():
        if tid != term.id:
            errs.append(f"id_mismatch:{tid}!={term.id}")
        for r in term.related_ids:
            if r not in TRADING_ONTOLOGY:
                errs.append(f"missing_related:{tid}->{r}")
    return errs
