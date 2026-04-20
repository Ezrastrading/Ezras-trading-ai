# Python SSL runtime (production)

## Problem

Apple’s **system Python** on macOS often links the `ssl` module against **LibreSSL**. **urllib3 v2** expects **OpenSSL 1.1.1+**. With LibreSSL you get `NotOpenSSLWarning` and unreliable HTTPS behavior under load — not acceptable for production.

**Do not** downgrade urllib3 to hide this. Fix the interpreter.

## Required runtime

- **Python:** 3.11.x (repo pins `3.11.8` in `.python-version`).
- **OpenSSL:** **OpenSSL ≥ 1.1.1** or **OpenSSL 3.x**, as reported by `ssl.OPENSSL_VERSION`.

## Recommended setup (macOS + Homebrew)

1. Install toolchain:

   ```bash
   brew install openssl@3 pyenv
   ```

2. Build Python against Homebrew OpenSSL:

   ```bash
   export LDFLAGS="-L$(brew --prefix openssl@3)/lib"
   export CPPFLAGS="-I$(brew --prefix openssl@3)/include"
   export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig"
   pyenv install -s 3.11.8
   ```

3. In the repo (`trading-ai/`):

   ```bash
   pyenv local 3.11.8
   eval "$(pyenv init -)"
   python -c "import ssl; print(ssl.OPENSSL_VERSION)"   # expect OpenSSL 3.x / 1.1.1+, not LibreSSL
   ```

4. Create a **fresh** venv from that Python only:

   ```bash
   rm -rf venv .venv
   python -m venv venv
   source venv/bin/activate
   python -m pip install -U pip setuptools wheel
   pip install -e ".[dev]"
   ```

## Verify

```bash
python -c "import ssl, sys, urllib3; print(sys.executable); print(ssl.OPENSSL_VERSION); print(urllib3.__version__)"
```

```bash
python -m trading_ai.deployment check-env
```

Look for `# ssl_guard_would_pass = True` and an `OpenSSL` line (not `LibreSSL`).

## Regression protection

`trading_ai.runtime_checks.ssl_guard.enforce_ssl()` runs at startup of `python -m trading_ai.deployment`. It **raises** if:

- `ssl.OPENSSL_VERSION` indicates **LibreSSL**, or  
- OpenSSL is present but **older than 1.1.1** (parsed from the version string).

This fails fast before network-heavy deployment work runs.
