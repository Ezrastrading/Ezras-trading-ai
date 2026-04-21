"""Shark: dual-mandate compounding + structural gap hunting.

Heavy CLI entrypoints are lazy-loaded so importing ``trading_ai.shark`` does not
pull execution wiring unless needed.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "main_shark",
    "sample_outputs_for_docs",
    "verify_shark_core_modules",
]


def verify_shark_core_modules() -> None:
    """
    Fail fast with an actionable error if the minimal shark layout is broken.

    Verifies absolute-import submodules resolve (``python -c "import trading_ai.shark.dotenv_load"``).
    """
    for mod in ("trading_ai.shark.dotenv_load", "trading_ai.shark.scheduler", "trading_ai.shark.lessons"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                f"Required shark submodule missing: {mod!r}. "
                f"Ensure PYTHONPATH includes the package ``src`` root (export PYTHONPATH=src)."
            ) from exc


def __getattr__(name: str) -> Any:
    if name in ("main_shark", "sample_outputs_for_docs"):
        from trading_ai.shark.cli import main_shark, sample_outputs_for_docs

        return {"main_shark": main_shark, "sample_outputs_for_docs": sample_outputs_for_docs}[name]
    raise AttributeError(name)
