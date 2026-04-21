"""Environment and path truth — honest about what Python cannot verify."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.runtime_paths import ezras_runtime_root, runtime_root_diagnostics


def run(*, runtime_root: Path) -> Dict[str, Any]:
    root = runtime_root
    diag = runtime_root_diagnostics()
    writable: List[str] = []
    for sub in ("data/control", "data/ledger", "data/review"):
        d = root / sub
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable.append(sub)
        except OSError:
            pass
    repo_root = Path(__file__).resolve().parents[3]
    git_sha = None
    try:
        git_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_root),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        git_sha = "unavailable_not_a_git_repo_or_git_missing"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ezras_runtime_root": str(ezras_runtime_root()),
        "runtime_root_param": str(root),
        "runtime_root_diagnostics": diag,
        "git_head": git_sha,
        "env_coinbase_set": bool(os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY")),
        "writable_subdirs_ok": writable,
        "operator_must_confirm": [
            "Deployment host matches EZRAS_RUNTIME_ROOT used by systemd/docker.",
            "Secrets are present only on the runtime host, not in repo.",
        ],
        "honesty": "Git SHA is best-effort; None if not a git checkout.",
    }
    write_control_json("deployment_truth_audit.json", payload, runtime_root=runtime_root)
    write_control_txt("deployment_truth_audit.txt", json.dumps(payload, indent=2) + "\n", runtime_root=runtime_root)
    return payload
