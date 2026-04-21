"""Trading knowledge and ontology (definitions, not execution)."""

from trading_ai.knowledge.avenue_models import avenue_structured, describe_avenue
from trading_ai.knowledge.profit_mechanics import (
    PRINCIPLES,
    avenue_explanation_card,
    explain_how_loss_happened,
    explain_how_profit_is_made,
    market_trade_reasoning,
)
from trading_ai.knowledge.trading_ontology import TRADING_ONTOLOGY, validate_ontology_internal
from trading_ai.knowledge.spot_inventory_ontology import (
    EXAMPLES as SPOT_INVENTORY_EXAMPLES,
    SpotPair,
    as_operator_card,
    parse_pair,
    realized_pnl_sell_usd,
    spot_equity_usd,
    unrealized_pnl_usd,
)

__all__ = [
    "PRINCIPLES",
    "TRADING_ONTOLOGY",
    "avenue_explanation_card",
    "avenue_structured",
    "describe_avenue",
    "explain_how_loss_happened",
    "explain_how_profit_is_made",
    "market_trade_reasoning",
    "validate_ontology_internal",
    "SPOT_INVENTORY_EXAMPLES",
    "SpotPair",
    "as_operator_card",
    "parse_pair",
    "realized_pnl_sell_usd",
    "spot_equity_usd",
    "unrealized_pnl_usd",
]
