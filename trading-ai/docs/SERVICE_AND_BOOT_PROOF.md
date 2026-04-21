## Service and boot proof (Ezra Trading AI) — non-live

This runbook proves **boot persistence**, **service persistence**, **role separation**, and **non-live safety** for the two-role operating system on a server with:

- **public repo**: `/opt/ezra-public`
- **private repo**: `/opt/ezra-private`
- **runtime root**: `/opt/ezra-runtime`
- **venv**: `/opt/ezra-venv`
- **overlay**: `PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src`

### 1) Install/repair systemd services (idempotent)

```bash
sudo PUBLIC_DIR=/opt/ezra-public \
  PRIVATE_DIR=/opt/ezra-private \
  RUNTIME_ROOT=/opt/ezra-runtime \
  VENV=/opt/ezra-venv \
  /opt/ezra-public/trading-ai/scripts/server/install_or_update_services.sh
```

Verify they’re enabled:

```bash
systemctl is-enabled ezra-ops.service
systemctl is-enabled ezra-research.service
```

### 2) One-time preflight (fail-closed)

This must exit non-zero if live flags are enabled, env files are missing, or imports fail.

```bash
source /opt/ezra-runtime/env/common.env
PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src \
  /opt/ezra-venv/bin/python /opt/ezra-public/trading-ai/scripts/server/deploy_preflight.py \
    --public-root /opt/ezra-public \
    --private-root /opt/ezra-private \
    --runtime-root /opt/ezra-runtime \
    --venv-root /opt/ezra-venv \
    --write-report
```

Expected artifact:
- `/opt/ezra-runtime/data/control/deploy_preflight.json`

### 3) Deployed-environment smoke (same environment as services)

```bash
PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src \
  /opt/ezra-venv/bin/python /opt/ezra-public/trading-ai/scripts/server/deployed_environment_smoke.py \
    --public-root /opt/ezra-public \
    --private-root /opt/ezra-private \
    --runtime-root /opt/ezra-runtime \
    --venv-root /opt/ezra-venv
```

Expected artifact:
- `/opt/ezra-runtime/data/control/deployed_environment_smoke.json`

### 4) Start services and verify steady state

```bash
sudo systemctl restart ezra-ops.service ezra-research.service
sudo systemctl --no-pager --full status ezra-ops.service
sudo systemctl --no-pager --full status ezra-research.service
```

Logs:

```bash
sudo journalctl -u ezra-ops.service -n 200 --no-pager
sudo journalctl -u ezra-research.service -n 200 --no-pager
```

### 5) Boot persistence proof (reboot)

```bash
sudo reboot
```

After reconnect:

```bash
systemctl is-enabled ezra-ops.service
systemctl is-enabled ezra-research.service
systemctl --no-pager --full status ezra-ops.service
systemctl --no-pager --full status ezra-research.service
```

Proof artifacts that should be “fresh” (timestamps advance after reboot):

- `/opt/ezra-runtime/data/control/operating_system/loop_status_ops.json`
- `/opt/ezra-runtime/data/control/operating_system/loop_status_research.json`

### 6) Fail-closed deploy procedure (dual-repo)

This script only restarts services if **preflight** and **smoke** pass.

```bash
sudo PUBLIC_REF=<public-commit-or-tag> PRIVATE_REF=<private-commit-or-tag> \
  PUBLIC_DIR=/opt/ezra-public PRIVATE_DIR=/opt/ezra-private \
  RUNTIME_ROOT=/opt/ezra-runtime VENV=/opt/ezra-venv \
  /opt/ezra-public/trading-ai/scripts/server/deploy_dual_repo.sh
```

Expected artifact:
- `/opt/ezra-runtime/data/control/deployed_refs.json`

### 7) Verify live is still disabled (non-negotiable)

```bash
systemctl show -p Environment ezra-ops.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED' || true
systemctl show -p Environment ezra-research.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED' || true
```

And re-run:
- `deploy_preflight.py` (must fail if live is enabled)
- `deployed_environment_smoke.py` (must report `live_disabled.ok=true`)

