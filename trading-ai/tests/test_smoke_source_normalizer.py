import pytest

from trading_ai.global_layer.source_normalizer import normalize_source


def test_normalize_source_common_schema():
    r = normalize_source(source_type="official_doc", title="Test", summary="S")
    assert r["source_id"]
    assert r["source_type"] == "official_doc"
    assert r["title"] == "Test"
