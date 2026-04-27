"""Kalshi Gate B high-probability classifier: crypto 15-minute and weather/politics streams."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Classification(Enum):
    """Trade classification."""
    REALISTIC_HIGH_PROBABILITY = "realistic_high_probability"
    FAKE_HIGH_PROBABILITY = "fake_high_probability"
    REJECT = "reject"


class RejectReason(Enum):
    """Reason for rejection."""
    FEE_DOMINATED_NO_EDGE = "fee_dominated_no_edge"
    OBVIOUS_NO_EDGE = "obvious_no_edge"
    THRESHOLD_TOO_FAR = "threshold_too_far"
    PRICE_NEAR_STRIKE_VOLATILE = "price_near_strike_volatile"
    NET_PROFIT_TOO_LOW = "net_profit_too_low"
    INSUFFICIENT_TIME = "insufficient_time"
    STALE_WEATHER_DATA = "stale_weather_data"
    AMBIGUOUS_LOCATION = "ambiguous_location"
    WEAK_EDGE = "weak_edge"
    OTHER = "other"


@dataclass
class CryptoMarketContext:
    """Context for crypto market classification."""
    current_price: float
    strike_price: float
    side_yes_or_no: str
    probability: float
    minutes_to_close: int
    distance_from_strike_pct: float
    recent_volatility: float
    spread: float
    fees: float
    payout: float


@dataclass
class WeatherMarketContext:
    """Context for weather market classification."""
    city: str
    hour: int
    day: str
    temperature: Optional[float]
    precipitation: Optional[float]
    wind: Optional[float]
    official_source: str
    source_timing: str
    market_wording: str
    probability: float
    minutes_to_close: int


@dataclass
class ClassificationResult:
    """Result of market classification."""
    market_id: str
    ticker: str
    classification: Classification
    decision: str
    reject_reason: Optional[RejectReason]
    expected_value: float
    net_profit_if_win: float
    loss_if_wrong: float
    gross_profit_if_win: float
    context: Dict[str, Any]


class GateBClassifier:
    """Kalshi Gate B high-probability classifier."""
    
    def __init__(self):
        self._min_probability = 0.85
        self._max_probability = 0.90
        self._min_minutes_to_close = 5
        self._max_minutes_to_close = 6
        self._min_net_profit = 0.01  # 1 cent minimum net profit
    
    def classify_crypto_market(
        self,
        market_id: str,
        ticker: str,
        context: CryptoMarketContext,
    ) -> ClassificationResult:
        """Classify crypto 15-minute market."""
        
        # Check time window
        if not (self._min_minutes_to_close <= context.minutes_to_close <= self._max_minutes_to_close):
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.REJECT,
                decision="REJECT",
                reject_reason=RejectReason.INSUFFICIENT_TIME,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._crypto_context_to_dict(context),
            )
        
        # Check probability range
        if not (self._min_probability <= context.probability <= self._max_probability):
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.OBVIOUS_NO_EDGE,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._crypto_context_to_dict(context),
            )
        
        # Calculate expected value
        gross_profit = context.payout - context.fees
        net_profit = gross_profit - context.spread
        expected_value = (context.probability * net_profit) - ((1 - context.probability) * context.loss_if_wrong if hasattr(context, 'loss_if_wrong') else 0)
        
        # Check if net profit is too low (fee-dominated)
        if net_profit <= self._min_net_profit:
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.FEE_DOMINATED_NO_EDGE,
                expected_value=expected_value,
                net_profit_if_win=net_profit,
                loss_if_wrong=0.0,
                gross_profit_if_win=gross_profit,
                context=self._crypto_context_to_dict(context),
            )
        
        # Check if threshold is too far from current price
        if context.distance_from_strike_pct > 0.20:  # More than 20% away
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.THRESHOLD_TOO_FAR,
                expected_value=expected_value,
                net_profit_if_win=net_profit,
                loss_if_wrong=0.0,
                gross_profit_if_win=gross_profit,
                context=self._crypto_context_to_dict(context),
            )
        
        # Check if price is near strike but volatile
        if context.distance_from_strike_pct < 0.02 and context.recent_volatility > 0.05:
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.PRICE_NEAR_STRIKE_VOLATILE,
                expected_value=expected_value,
                net_profit_if_win=net_profit,
                loss_if_wrong=0.0,
                gross_profit_if_win=gross_profit,
                context=self._crypto_context_to_dict(context),
            )
        
        # Check expected value
        if expected_value <= 0:
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.WEAK_EDGE,
                expected_value=expected_value,
                net_profit_if_win=net_profit,
                loss_if_wrong=0.0,
                gross_profit_if_win=gross_profit,
                context=self._crypto_context_to_dict(context),
            )
        
        # All checks passed - realistic high probability
        return ClassificationResult(
            market_id=market_id,
            ticker=ticker,
            classification=Classification.REALISTIC_HIGH_PROBABILITY,
            decision="ALLOW",
            reject_reason=None,
            expected_value=expected_value,
            net_profit_if_win=net_profit,
            loss_if_wrong=0.0,
            gross_profit_if_win=gross_profit,
            context=self._crypto_context_to_dict(context),
        )
    
    def classify_weather_market(
        self,
        market_id: str,
        ticker: str,
        context: WeatherMarketContext,
    ) -> ClassificationResult:
        """Classify weather/politics market."""
        
        # Check time window (5-10 minutes before close)
        if not (5 <= context.minutes_to_close <= 10):
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.REJECT,
                decision="REJECT",
                reject_reason=RejectReason.INSUFFICIENT_TIME,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._weather_context_to_dict(context),
            )
        
        # Check for stale or ambiguous weather data
        if not context.official_source or context.source_timing == "stale":
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.REJECT,
                decision="REJECT",
                reject_reason=RejectReason.STALE_WEATHER_DATA,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._weather_context_to_dict(context),
            )
        
        if not context.city or context.city == "unknown":
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.REJECT,
                decision="REJECT",
                reject_reason=RejectReason.AMBIGUOUS_LOCATION,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._weather_context_to_dict(context),
            )
        
        # Check probability range
        if context.probability < 0.85:
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.FAKE_HIGH_PROBABILITY,
                decision="REJECT",
                reject_reason=RejectReason.WEAK_EDGE,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._weather_context_to_dict(context),
            )
        
        # For weather, we need source evidence matching market wording
        # This is a simplified check - in production, you'd fetch real weather data
        if context.market_wording and context.city.lower() in context.market_wording.lower():
            return ClassificationResult(
                market_id=market_id,
                ticker=ticker,
                classification=Classification.REALISTIC_HIGH_PROBABILITY,
                decision="ALLOW",
                reject_reason=None,
                expected_value=0.0,
                net_profit_if_win=0.0,
                loss_if_wrong=0.0,
                gross_profit_if_win=0.0,
                context=self._weather_context_to_dict(context),
            )
        
        return ClassificationResult(
            market_id=market_id,
            ticker=ticker,
            classification=Classification.REJECT,
            decision="REJECT",
            reject_reason=RejectReason.OTHER,
            expected_value=0.0,
            net_profit_if_win=0.0,
            loss_if_wrong=0.0,
            gross_profit_if_win=0.0,
            context=self._weather_context_to_dict(context),
        )
    
    def _crypto_context_to_dict(self, context: CryptoMarketContext) -> Dict[str, Any]:
        """Convert crypto context to dict for logging."""
        return {
            "current_price": context.current_price,
            "strike_price": context.strike_price,
            "side_yes_or_no": context.side_yes_or_no,
            "probability": context.probability,
            "minutes_to_close": context.minutes_to_close,
            "distance_from_strike_pct": context.distance_from_strike_pct,
            "recent_volatility": context.recent_volatility,
            "spread": context.spread,
            "fees": context.fees,
            "payout": context.payout,
        }
    
    def _weather_context_to_dict(self, context: WeatherMarketContext) -> Dict[str, Any]:
        """Convert weather context to dict for logging."""
        return {
            "city": context.city,
            "hour": context.hour,
            "day": context.day,
            "temperature": context.temperature,
            "precipitation": context.precipitation,
            "wind": context.wind,
            "official_source": context.official_source,
            "source_timing": context.source_timing,
            "market_wording": context.market_wording,
            "probability": context.probability,
            "minutes_to_close": context.minutes_to_close,
        }


# Global classifier instance
_gate_b_classifier = GateBClassifier()


def classify_crypto_market(
    market_id: str,
    ticker: str,
    context: CryptoMarketContext,
) -> ClassificationResult:
    """Classify crypto market using global classifier."""
    result = _gate_b_classifier.classify_crypto_market(market_id, ticker, context)
    logger.info(
        "Crypto classification: market=%s ticker=%s classification=%s decision=%s reject_reason=%s",
        market_id,
        ticker,
        result.classification.value,
        result.decision,
        result.reject_reason.value if result.reject_reason else None,
    )
    return result


def classify_weather_market(
    market_id: str,
    ticker: str,
    context: WeatherMarketContext,
) -> ClassificationResult:
    """Classify weather market using global classifier."""
    result = _gate_b_classifier.classify_weather_market(market_id, ticker, context)
    logger.info(
        "Weather classification: market=%s ticker=%s classification=%s decision=%s reject_reason=%s",
        market_id,
        ticker,
        result.classification.value,
        result.decision,
        result.reject_reason.value if result.reject_reason else None,
    )
    return result
