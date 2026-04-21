## Deployment boot persistence proof (Ezra Trading AI)

This runbook proves **boot persistence** and **non-live safety** for the two-role operating system on a server with:

- **public repo**: `/opt/ezra-public`
- **private repo**: `/opt/ezra-private`
- **runtime root**: `/opt/ezra-runtime`
- **venv**: `/opt/ezra-venv`
- **overlay**: `PYTHONPATH=/opt/ezra-private/trading-ai/src:/opt/ezra-public/trading-ai/src`

### 1) Install/repair systemd services (idempotent)

Copy/update the unit files and enable them on boot:

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

This must **exit non-zero** if live flags are enabled, env files are missing, or imports fail.

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

This must run without an interactive shell dependency and must write a final report.

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

Proof artifacts that should be “fresh” (timestamps should advance after reboot):

- `/opt/ezra-runtime/data/control/operating_system/loop_status_ops.json`
- `/opt/ezra-runtime/data/control/operating_system/loop_status_research.json`
- `/opt/ezra-runtime/data/control/deployed_environment_smoke.json`

### 6) Safe deploy/update procedure (dual-repo)

This deploy script only restarts services if both **preflight** and **smoke** pass.

```bash
sudo PUBLIC_REF=<public-commit-or-tag> PRIVATE_REF=<private-commit-or-tag> \
  PUBLIC_DIR=/opt/ezra-public PRIVATE_DIR=/opt/ezra-private \
  RUNTIME_ROOT=/opt/ezra-runtime VENV=/opt/ezra-venv \
  /opt/ezra-public/trading-ai/scripts/server/deploy_dual_repo.sh
```

Expected artifact:
- `/opt/ezra-runtime/data/control/deployed_refs.json`

### 7) Rollback (fail-safe)

1) Checkout prior known-good refs in both repos:

```bash
sudo PUBLIC_REF=<prior-public-sha> PRIVATE_REF=<prior-private-sha> \
  /opt/ezra-public/trading-ai/scripts/server/deploy_dual_repo.sh
```

2) If needed, stop services first:

```bash
sudo systemctl stop ezra-ops.service ezra-research.service
```

### 8) Verify live is still disabled (non-negotiable)

Hard checks:

```bash
systemctl show -p Environment ezra-ops.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED' || true
systemctl show -p Environment ezra-research.service | tr ' ' '\n' | egrep 'NTE_EXECUTION_MODE|NTE_LIVE_TRADING_ENABLED|COINBASE_EXECUTION_ENABLED' || true
```

And re-run:
- `deploy_preflight.py` (must fail if live is enabled)
- `deployed_environment_smoke.py` (must report `live_disabled.ok=true`)

