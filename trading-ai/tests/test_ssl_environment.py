"""SSL runtime expectations: OpenSSL 1.1.1+ / 3.x; no LibreSSL (urllib3 v2)."""

from __future__ import annotations

import ssl

import pytest

from trading_ai.runtime_checks.ssl_guard import (
    enforce_ssl,
    is_acceptable_openssl,
    parse_openssl_version_string,
)


def test_host_openssl_is_not_libressl() -> None:
    assert "libressl" not in ssl.OPENSSL_VERSION.lower()


def test_parse_openssl_version_string_samples() -> None:
    assert parse_openssl_version_string("OpenSSL 3.6.1 27 Jan 2026") == (3, 6, 1)
    assert parse_openssl_version_string("OpenSSL 1.1.1w  11 Sep 2023") == (1, 1, 1)
    assert parse_openssl_version_string("LibreSSL 2.8.3") is None


def test_is_acceptable_openssl_matrix() -> None:
    assert is_acceptable_openssl("OpenSSL 3.0.12 24 Oct 2023") is True
    assert is_acceptable_openssl("OpenSSL 1.1.1w  11 Sep 2023") is True
    assert is_acceptable_openssl("OpenSSL 1.1.0l  10 Sep 2019") is False
    assert is_acceptable_openssl("LibreSSL 2.8.3") is False


def test_enforce_ssl_passes_on_current_interpreter() -> None:
    enforce_ssl()


def test_enforce_ssl_rejects_libressl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssl, "OPENSSL_VERSION", "LibreSSL 2.8.3")
    with pytest.raises(RuntimeError, match="LibreSSL|libressl|Incompatible"):
        enforce_ssl()


def test_enforce_ssl_rejects_openssl_1_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssl, "OPENSSL_VERSION", "OpenSSL 1.0.2u  20 Dec 2019")
    with pytest.raises(RuntimeError, match="Unsupported"):
        enforce_ssl()
