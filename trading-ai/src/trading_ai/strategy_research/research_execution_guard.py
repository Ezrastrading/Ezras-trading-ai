"""Blocks consumption of strategy research output from execution-adjacent code paths."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

RESEARCH_EXECUTION_BAN_MSG = "RESEARCH CANNOT DRIVE EXECUTION"

F = TypeVar("F", bound=Callable[..., Any])


def _norm_path(path: str) -> str:
    return path.replace("\\", "/")


def _forbidden_stack_frame(frame: Any) -> bool:
    """True if frame is in coinbase_engine, execution_live, or governance packages."""
    path = ""
    try:
        path = _norm_path(frame.f_code.co_filename)
    except Exception:
        return False

    if path.endswith("coinbase_engine.py") or "/coinbase_engine.py" in path:
        return True
    if path.endswith("execution_live.py") or "/execution_live.py" in path:
        return True
    if f"{os.sep}governance{os.sep}" in frame.f_code.co_filename:
        return True
    if "/governance/" in path:
        return True

    mod = inspect.getmodule(frame)
    name = getattr(mod, "__name__", "") or ""
    if name.startswith("trading_ai.governance"):
        return True
    if name.startswith("trading_ai.shark.governance"):
        return True
    return False


def assert_strategy_research_read_allowed() -> None:
    """
    Call at the start of any API that returns strategy research artifacts.

    Raises Exception(RESEARCH_EXECUTION_BAN_MSG) if the active call stack includes execution
    or governance modules (dashboards / analysis / manual review are unaffected).
    """
    for fr in inspect.stack()[1:]:
        try:
            if _forbidden_stack_frame(fr.frame):
                raise Exception(RESEARCH_EXECUTION_BAN_MSG)
        finally:
            del fr


def research_read_guarded(fn: F) -> F:
    """Decorator that enforces :func:`assert_strategy_research_read_allowed`."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        assert_strategy_research_read_allowed()
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def safe_read_text_file(path: Path) -> str:
    """Read a file for dashboards/analysis only — blocked from execution contexts."""
    assert_strategy_research_read_allowed()
    return path.read_text(encoding="utf-8")
