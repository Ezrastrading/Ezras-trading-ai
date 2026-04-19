"""
CDP REST JWT ``uri`` must match coinbase-advanced-py: path without query string.

Wrong: ``GET api.coinbase.com/.../accounts?limit=250`` → 401 on authenticated GET.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading_ai.shark.outlets.coinbase import CoinbaseClient


def test_request_signs_jwt_path_without_query_params() -> None:
    """When GET has params, HTTP URL keeps ?… but JWT uri claim must not (SDK-compatible)."""
    c = CoinbaseClient.__new__(CoinbaseClient)
    c.base_url = "https://api.coinbase.com/api/v3/brokerage"
    c.marked_down = False
    c._sync_credentials_from_env = lambda: None
    c._credentials_ready = lambda: True
    c.last_error = ""

    jwt_paths: list[str] = []

    def capture_jwt(_self, method: str, request_path: str) -> str:
        jwt_paths.append(request_path)
        return "h.p.sig"

    fake_resp = MagicMock()
    fake_resp.read.return_value = b"{}"
    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_resp
    fake_ctx.__exit__.return_value = None

    with patch.object(CoinbaseClient, "_build_jwt", capture_jwt):
        with patch("urllib.request.urlopen", return_value=fake_ctx):
            CoinbaseClient._request(c, "GET", "/accounts", params={"limit": 250, "cursor": "abc"})

    assert len(jwt_paths) == 1
    assert jwt_paths[0] == "/api/v3/brokerage/accounts"
    assert "?" not in jwt_paths[0]

