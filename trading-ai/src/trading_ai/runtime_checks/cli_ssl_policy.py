"""When to run ``enforce_ssl()`` for ``python -m trading_ai`` — centralized, documented allowlist."""

from __future__ import annotations

from typing import FrozenSet

from trading_ai.runtime_checks.ssl_guard import enforce_ssl

# Subcommands that only introspect env vars, read/write local files, or static analysis — no outbound HTTPS.
_PRIMARY_CLI_SSL_EXEMPT: FrozenSet[str] = frozenset(
    {
        "validate-env",
        "audit-env",
        "record-decision",
        "export-metrics",
        "phase6-prep-status",
        "storage",
        "automation-scope",
        "connectivity-audit",
        "integrity-check",
    }
)


def enforce_ssl_for_primary_cli_command(cmd: str | None) -> None:
    """
    Fail fast on LibreSSL / legacy OpenSSL before urllib3-backed HTTPS for network-capable CLI paths.

    Exempts a small set of purely local/bootstrap subcommands so operators can run ``validate-env`` /
    ``audit-env`` while still on a broken system Python (then fix interpreter per docs/SSL_RUNTIME.md).
    """
    if not cmd or cmd in _PRIMARY_CLI_SSL_EXEMPT:
        return
    enforce_ssl()
