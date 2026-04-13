#!/usr/bin/env bash
set -euo pipefail
echo "Deploying Ezras Shark to Railway..."

if ! command -v railway &>/dev/null; then
  echo "Install Railway CLI: npm install -g @railway/cli"
  exit 1
fi

echo "Run: railway login  (once)"
echo "Then: railway up"
railway up

echo "Deployed. Monitor with: railway logs --follow"
