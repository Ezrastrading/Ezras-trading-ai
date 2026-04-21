from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(script: Path, *, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)


def test_server_smoke_and_readiness_scripts_write_json() -> None:
    with tempfile.TemporaryDirectory(prefix="ezra_server_gate_test_") as td:
        root = Path(td).resolve()
        env = dict(os.environ)
        env["EZRAS_RUNTIME_ROOT"] = str(root)
        # Force non-live defaults (scripts also assert).
        env["NTE_EXECUTION_MODE"] = "paper"
        env["NTE_LIVE_TRADING_ENABLED"] = "false"
        env["COINBASE_EXECUTION_ENABLED"] = "false"
        env.pop("COINBASE_ENABLED", None)
        env.setdefault("GOVERNANCE_ORDER_ENFORCEMENT", "false")

        trading_ai_repo = Path(__file__).resolve().parents[1]
        workspace = trading_ai_repo.parent
        env["PYTHONPATH"] = (
            str(trading_ai_repo / "src")
            + os.pathsep
            + str(trading_ai_repo)
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )

        # Minimal runtime env contract (deploy_preflight expects this tree).
        env_dir = root / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "common.env").write_text(
            "NTE_EXECUTION_MODE=paper\nNTE_LIVE_TRADING_ENABLED=false\nCOINBASE_EXECUTION_ENABLED=false\n",
            encoding="utf-8",
        )

        # Minimal fake venv so deploy_preflight can pass without /opt/ezra-venv.
        fake_venv = Path(td) / "fake_venv"
        (fake_venv / "bin").mkdir(parents=True, exist_ok=True)
        exe = Path(sys.executable).resolve()
        for name in ("python3", "python"):
            p = fake_venv / "bin" / name
            try:
                p.symlink_to(exe, target_is_directory=False)
            except FileExistsError:
                pass

        base = Path(__file__).resolve().parents[1] / "scripts" / "server"
        smoke = base / "deployed_environment_smoke.py"
        micro = base / "micro_trade_readiness.py"
        final = base / "final_switch_readiness.py"

        # Smoke will run supervisors and should write deployed_environment_smoke.json under our temp runtime root.
        cp = subprocess.run(
            [
                sys.executable,
                str(smoke),
                "--runtime-root",
                str(root),
                "--public-root",
                str(workspace),
                "--private-root",
                str(workspace),
                "--venv-root",
                str(fake_venv),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert cp.returncode == 0, cp.stderr + "\n" + cp.stdout
        out = root / "data" / "control" / "deployed_environment_smoke.json"
        assert out.is_file()
        _ = json.loads(out.read_text(encoding="utf-8"))
        assert (root / "data" / "control" / "deploy_preflight.json").is_file()

        cp2 = subprocess.run(
            [
                sys.executable,
                str(micro),
                "--runtime-root",
                str(root),
                "--public-root",
                str(workspace),
                "--private-root",
                str(workspace),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert (root / "data" / "control" / "micro_trade_readiness.json").is_file()
        assert cp2.returncode == 0, cp2.stderr + "\n" + cp2.stdout

        cp3 = subprocess.run([sys.executable, str(final), "--runtime-root", str(root)], env=env, capture_output=True, text=True, check=False)
        assert (root / "data" / "control" / "final_switch_readiness.json").is_file()
        assert cp3.returncode == 0, cp3.stderr + "\n" + cp3.stdout

