"""Shark (public-safe shell).

The full Shark execution stack lives in the private repo. Public builds should be able to
import lightweight utilities (dotenv loading, lessons/mission helpers, optional Supabase
logging) without pulling in private execution logic.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "main_shark",
    "sample_outputs_for_docs",
]


def __getattr__(name: str) -> Any:
    if name in ("main_shark", "sample_outputs_for_docs"):
        try:
            from trading_ai.shark.cli import main_shark, sample_outputs_for_docs

            return {"main_shark": main_shark, "sample_outputs_for_docs": sample_outputs_for_docs}[name]
        except Exception as e:  # pragma: no cover
            raise AttributeError(
                f"{name} is not available in the public build (private shark.cli missing): {e}"
            ) from e
    raise AttributeError(name)

