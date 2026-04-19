"""Atomic JSON writes (temp + replace) to reduce torn writes on crash."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, data: Dict[str, Any], *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=indent, default=str)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
