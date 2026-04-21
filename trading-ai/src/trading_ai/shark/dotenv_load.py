"""Load `.env` before reading `os.environ` (idempotent; safe if python-dotenv missing)."""

from __future__ import annotations

from pathlib import Path

_LOADED = False


def load_shark_dotenv() -> None:
    """Search project root and cwd for `.env` and merge into the process environment."""
    global _LOADED
    if _LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOADED = True
        return

    here = Path(__file__).resolve()
    candidates: list[Path] = [Path.cwd() / ".env"]
    p = here.parent
    for _ in range(6):
        candidates.append(p / ".env")
        if p.parent == p:
            break
        p = p.parent
    for env_path in candidates:
        if env_path.is_file():
            load_dotenv(env_path)
            _LOADED = True
            return
    load_dotenv()
    _LOADED = True

