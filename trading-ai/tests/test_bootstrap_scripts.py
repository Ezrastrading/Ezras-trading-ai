"""Bootstrap shell scripts are syntactically valid."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_bootstrap_scripts_pass_shellcheck_syntax() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("bootstrap_runtime.sh", "create_venv.sh"):
        p = root / "scripts" / name
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
