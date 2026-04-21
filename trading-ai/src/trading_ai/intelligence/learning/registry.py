"""Learning registry — domain index and version pointers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.intelligence.learning.domain_catalog import DOMAIN_IDS
from trading_ai.intelligence.paths import learning_registry_json_path


def default_registry() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "domains": [
            {
                "domain_id": d,
                "json_path": f"data/learning/domains/{d}.json",
                "txt_path": f"data/learning/domains/{d}.txt",
            }
            for d in DOMAIN_IDS
        ],
        "honesty_note": "Entries are hypotheses until backed by tickets and measured evidence.",
    }


def load_or_init_registry(runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    p = learning_registry_json_path(runtime_root=runtime_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        reg = default_registry()
        p.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return reg
    return json.loads(p.read_text(encoding="utf-8"))


def save_registry(reg: Dict[str, Any], runtime_root: Optional[Path] = None) -> None:
    p = learning_registry_json_path(runtime_root=runtime_root)
    p.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
