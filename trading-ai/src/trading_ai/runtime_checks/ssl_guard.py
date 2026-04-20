"""Fail fast when Python's ssl is linked against LibreSSL or OpenSSL older than 1.1.1."""

from __future__ import annotations

import re
import ssl
from typing import Any, Dict, Optional, Tuple


def parse_openssl_version_string(version: str) -> Optional[Tuple[int, int, int]]:
    """
    Parse (major, minor, patch) from ssl.OPENSSL_VERSION-style strings.

    Returns None if the string is not a recognizable OpenSSL release (e.g. LibreSSL).
    """
    if not version or "libressl" in version.lower():
        return None
    m = re.search(r"OpenSSL\s+(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_acceptable_openssl(version: str) -> bool:
    """True for OpenSSL >= 1.1.1 (including 3.x). False for LibreSSL, missing parse, or too-old OpenSSL."""
    t = parse_openssl_version_string(version)
    if t is None:
        return False
    major, minor, patch = t
    if major >= 3:
        return True
    if major == 1 and (minor, patch) >= (1, 1):
        return True
    return False


def ssl_runtime_diagnostic() -> Dict[str, Any]:
    """
    Structured SSL facts for operators (never raises). Use with check-env / health output.
    """
    ver = ssl.OPENSSL_VERSION
    ok = is_acceptable_openssl(ver)
    return {
        "python_executable": __import__("sys").executable,
        "python_version": __import__("sys").version.split()[0],
        "ssl_openssl_version": ver,
        "ssl_guard_would_pass": ok,
        "parsed_openssl_tuple": parse_openssl_version_string(ver),
    }


def enforce_ssl() -> None:
    """
    Production guard: urllib3 v2 expects OpenSSL 1.1.1+; LibreSSL and legacy OpenSSL are rejected.

    Call once at process entry (e.g. ``python -m trading_ai.deployment`` or network-capable
    ``python -m trading_ai`` subcommands via ``cli_ssl_policy``) after selecting a Python
    built against Homebrew OpenSSL (see docs/SSL_RUNTIME.md).
    """
    ver = ssl.OPENSSL_VERSION
    if "libressl" in ver.lower():
        raise RuntimeError(
            f"Incompatible SSL: {ver}. urllib3 v2 requires OpenSSL 1.1.1+. "
            "Install Python via pyenv with openssl@3 (LDFLAGS/CPPFLAGS); see docs/SSL_RUNTIME.md."
        )
    if not is_acceptable_openssl(ver):
        raise RuntimeError(
            f"Unsupported OpenSSL for production HTTPS: {ver!r}. "
            "Require OpenSSL >= 1.1.1 (or 3.x). Use pyenv + Homebrew openssl@3; see docs/SSL_RUNTIME.md."
        )
