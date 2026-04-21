#!/usr/bin/env python3
"""
Dual-repo safety: fail when a *public* clone incorrectly vendors ``trading-ai/`` source.

Set ``EZRAS_REPO_KIND=public`` in CI for the public mirror; private dev leaves unset (check skipped).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-root", default=None)
    args = ap.parse_args()

    from trading_ai.runtime_paths import ezras_runtime_root

    root = Path(args.runtime_root).resolve() if args.runtime_root else ezras_runtime_root()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)

    kind = (os.environ.get("EZRAS_REPO_KIND") or "").strip().lower()
    cwd = Path.cwd()
    trading_src = cwd / "trading-ai" / "src" / "trading_ai"
    leaked = kind == "public" and trading_src.is_dir()

    doc = {
        "truth_version": "repo_separation_verify_v1",
        "repo_kind": kind or "private_or_unset",
        "cwd": str(cwd),
        "trading_ai_tree_present": trading_src.is_dir(),
        "ok": not leaked,
        "honesty": "Set EZRAS_REPO_KIND=public only on the public mirror CI; private repos may keep trading-ai/.",
    }
    if leaked:
        doc["error"] = "public_repo_must_not_vendor_trading_ai_src"

    p = ctrl / "repo_separation_verify.json"
    p.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(doc, indent=2, sort_keys=True, default=str))
    return 0 if doc["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
