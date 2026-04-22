from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_deployed_environment_smoke_writes_report(tmp_path: Path, monkeypatch) -> None:
    # Use local repo as both public/private roots for deterministic run.
    repo_root = Path(__file__).resolve().parents[1]
    public_root = tmp_path / "opt" / "ezra-public"
    private_root = tmp_path / "opt" / "ezra-private"
    runtime_root = tmp_path / "opt" / "ezra-runtime"
    venv_root = tmp_path / "opt" / "ezra-venv"

    # Layout with "trading-ai" symlink-like copy by pointing roots at real repo.
    # We create directories and then bind by copying minimal files needed.
    (public_root / "trading-ai").mkdir(parents=True, exist_ok=True)
    (private_root / "trading-ai").mkdir(parents=True, exist_ok=True)
    (runtime_root / "env").mkdir(parents=True, exist_ok=True)
    (runtime_root / "env" / "common.env").write_text("NTE_EXECUTION_MODE=paper\n", encoding="utf-8")
    (venv_root / "bin").mkdir(parents=True, exist_ok=True)
    (venv_root / "bin" / "python").write_text("# fake\n", encoding="utf-8")

    # Point to real source + server scripts by symlinking into the expected location (overlay layout).
    for root in (public_root, private_root):
        trading = root / "trading-ai"
        trading.mkdir(parents=True, exist_ok=True)
        for name in ("src", "scripts"):
            tgt = trading / name
            if tgt.exists():
                continue
            try:
                tgt.symlink_to(repo_root / name)
            except Exception:
                if name == "src":
                    tgt.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NTE_EXECUTION_MODE", "paper")
    monkeypatch.setenv("NTE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "false")

    smoke = repo_root / "scripts" / "server" / "deployed_environment_smoke.py"
    assert smoke.is_file()

    # Ensure PYTHONPATH matches overlay contract.
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{private_root}/trading-ai/src:{public_root}/trading-ai/src"

    res = subprocess.run(
        [sys.executable, str(smoke), "--public-root", str(public_root), "--private-root", str(private_root), "--runtime-root", str(runtime_root), "--venv-root", str(venv_root)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # We only require that a report is written deterministically; exit code may be non-zero if some artifacts aren't created in this minimal layout.
    out = runtime_root / "data" / "control" / "deployed_environment_smoke.json"
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["truth_version"] == "deployed_environment_smoke_v1"
    assert payload.get("live_micro_private_build", {}).get("ok") is True

