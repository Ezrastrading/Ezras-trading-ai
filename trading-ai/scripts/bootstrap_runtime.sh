#!/usr/bin/env bash
# Reproducible macOS/Linux bootstrap: Homebrew OpenSSL 3 + pyenv Python 3.11.8 + project venv.
# Does not weaken SSL checks — use a Python linked against OpenSSL 1.1.1+ / 3.x (not LibreSSL).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. On macOS install from https://brew.sh — or follow manual steps in docs/SSL_RUNTIME.md"
  exit 1
fi

echo "==> Repo: $ROOT"
echo "==> Expect: Homebrew openssl@3 and pyenv (install manually if missing)."
echo "    brew install openssl@3 pyenv"

export LDFLAGS="-L$(brew --prefix openssl@3)/lib"
export CPPFLAGS="-I$(brew --prefix openssl@3)/include"
export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig"

PY_VER="${PY_VER:-3.11.8}"
echo "==> pyenv install -s $PY_VER"
pyenv install -s "$PY_VER"
pyenv local "$PY_VER"
eval "$(pyenv init -)"

echo "==> SSL check (expect OpenSSL, not LibreSSL)"
python -c "import ssl; print(ssl.OPENSSL_VERSION)"

bash "$ROOT/scripts/create_venv.sh"

echo "==> Smoke: deployment check-env (non-fatal if env incomplete)"
PYTHONPATH=src python -m trading_ai.deployment check-env || true

echo "==> Done. Activate: source venv/bin/activate"
