"""
Abstract storage for runtime artifacts. All paths are relative to ``EZRAS_RUNTIME_ROOT`` (or explicit root).

Local adapter is the default. S3 is reserved for a future backend — do not import boto here.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class StorageAdapter(ABC):
    """Read/write by logical key under the deployment runtime root."""

    @abstractmethod
    def root(self) -> Path:
        ...

    @abstractmethod
    def read_text(self, relative_path: str, *, encoding: str = "utf-8") -> str:
        ...

    @abstractmethod
    def write_text(self, relative_path: str, content: str, *, encoding: str = "utf-8") -> None:
        ...

    def read_json(self, relative_path: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.read_text(relative_path)
        except OSError:
            return None
        try:
            out = json.loads(raw)
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            return None

    def write_json(self, relative_path: str, payload: Dict[str, Any], *, indent: int = 2) -> None:
        self.write_text(relative_path, json.dumps(payload, indent=indent, default=str) + "\n")

    def ensure_parent(self, relative_path: str) -> None:
        p = (self.root() / relative_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

    def exists(self, relative_path: str) -> bool:
        return (self.root() / relative_path).is_file()


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, runtime_root: Optional[Path] = None) -> None:
        from trading_ai.runtime_paths import ezras_runtime_root

        self._root = Path(runtime_root or ezras_runtime_root()).resolve()

    def root(self) -> Path:
        return self._root

    def read_text(self, relative_path: str, *, encoding: str = "utf-8") -> str:
        p = (self._root / relative_path).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError("path_escape")
        return p.read_text(encoding=encoding)

    def write_text(self, relative_path: str, content: str, *, encoding: str = "utf-8") -> None:
        p = (self._root / relative_path).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError("path_escape")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)


class S3StorageAdapter(StorageAdapter):
    """
    Placeholder for cloud sync. Raises if constructed until wired with credentials + bucket.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError(
            "S3StorageAdapter is not implemented yet — use LocalStorageAdapter (default)."
        )

    def root(self) -> Path:
        raise NotImplementedError

    def read_text(self, relative_path: str, *, encoding: str = "utf-8") -> str:
        raise NotImplementedError

    def write_text(self, relative_path: str, content: str, *, encoding: str = "utf-8") -> None:
        raise NotImplementedError


def get_storage_adapter() -> StorageAdapter:
    backend = (os.environ.get("EZRAS_STORAGE_BACKEND") or "local").strip().lower()
    if backend in ("s3", "remote"):
        raise NotImplementedError(
            "EZRAS_STORAGE_BACKEND=s3 is not wired yet; use local (default) or implement S3StorageAdapter."
        )
    return LocalStorageAdapter()
