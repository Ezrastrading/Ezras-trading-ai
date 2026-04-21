"""Runtime root resolution for daemon authority (``~/``, ``.../~/...`` typos)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_resolve_ezras_runtime_root_for_daemon_authority_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trading_ai.runtime_paths import resolve_ezras_runtime_root_for_daemon_authority

    home = Path.home()
    expected = (home / "ezras-runtime").resolve()
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(home / "ezras-runtime"))
    assert resolve_ezras_runtime_root_for_daemon_authority(None) == expected
