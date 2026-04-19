import pytest

from trading_ai.global_layer.supabase_runtime_reader import read_supabase_snapshot


def test_supabase_reader_no_crash_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    out = read_supabase_snapshot()
    assert out["connected"] is False
    assert "supabase_credentials" in out["missing_sources"]
