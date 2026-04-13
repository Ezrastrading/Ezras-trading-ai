from __future__ import annotations

from trading_ai.config import Settings
from trading_ai.models.schemas import CandidateMarket


def filter_candidates(
    markets: list[CandidateMarket],
    settings: Settings,
) -> list[CandidateMarket]:
    out: list[CandidateMarket] = []
    for m in markets:
        if m.volume_usd is None:
            continue
        if m.volume_usd < settings.min_volume_usd:
            continue
        if settings.require_implied_probability and m.implied_probability is None:
            continue
        if settings.max_days_to_expiry is not None:
            if m.days_to_expiry is None:
                continue
            if m.days_to_expiry < 0 or m.days_to_expiry > settings.max_days_to_expiry:
                continue
        p = m.implied_probability
        if p is not None:
            if p < settings.min_implied_prob or p > settings.max_implied_prob:
                continue
        out.append(m)
    return out
