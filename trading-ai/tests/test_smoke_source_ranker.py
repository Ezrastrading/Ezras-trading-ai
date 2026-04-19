import pytest

from trading_ai.global_layer.source_quality_ranker import rank_sources


def test_rank_sources_orders_official_above_other():
    cands = [
        {
            "source_id": "a",
            "source_type": "other",
            "title": "x",
            "summary": "",
        },
        {
            "source_id": "b",
            "source_type": "official_doc",
            "title": "Venue API",
            "summary": "",
            "url": "https://example.com",
        },
    ]
    ranked = rank_sources(cands)
    assert ranked[0]["source_type"] == "official_doc"
