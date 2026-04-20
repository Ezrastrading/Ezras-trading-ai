# Trading AI (Phase 1)

Independent AI trading partner for prediction markets: monitor Polymarket, enrich with news and sources, produce structured trade briefs, alert on high-signal candidates, and log everything. **Execution is human-confirmed** — no auto-trading.

This project lives only under `trading-ai/` and is not coupled to Nexplora.

## Project separation guarantee

This system is **fully independent** from Nexplora. It does not read, write, import, or depend on the Nexplora codebase, configuration, or databases. All code and data for this trading partner live under `trading-ai/` only.

## Setup

1. **Python 3.11** — use the version in `.python-version` (e.g. via **pyenv**). On macOS, avoid the system Python for this repo: it is often linked to **LibreSSL**, which breaks **urllib3 v2**. Build Python against **Homebrew OpenSSL 3** and verify `ssl.OPENSSL_VERSION` shows **OpenSSL**, not LibreSSL. See **[docs/SSL_RUNTIME.md](docs/SSL_RUNTIME.md)**.

2. **Create a virtual environment** (recommended):

   ```bash
   cd trading-ai
   eval "$(pyenv init -)"   # if using pyenv
   python -m venv venv
   source venv/bin/activate
   pip install -U pip setuptools wheel
   pip install -e ".[dev]"
   ```

3. **Configure environment**:

   ```bash
   cp .env.example .env
   # Edit .env — at minimum set OPENAI_API_KEY
   ```

4. **Validate configuration**:

   ```bash
   cd trading-ai
   PYTHONPATH=src python scripts/validate_env.py
   ```

   Or after install: `python -m trading_ai validate-env`

## Run

From `trading-ai` with `.env` present:

- **One pipeline cycle** (fetch → filter → enrich → brief → alert if score high → SQLite log):

  ```bash
  PYTHONPATH=src python -m trading_ai run
  ```

- **Markets only** (no API keys for Tavily/OpenAI):

  ```bash
  PYTHONPATH=src python -m trading_ai run --dry-market-only
  ```

- **Scheduled runs** (set `SCHEDULE_INTERVAL_MINUTES` in `.env`):

  ```bash
  PYTHONPATH=src python -m trading_ai schedule
  ```

- **Record a human decision** (after reviewing a brief):

  ```bash
  PYTHONPATH=src python -m trading_ai record-decision \
    --market-id "<id>" \
    --brief-created-at "2026-04-11T12:00:00+00:00" \
    --action watch \
    --notes "Revisit after earnings"
  ```

SQLite database path: `data/trading_ai.sqlite` (configurable via `DATA_DIR`).

## Phase 1 default filters

Defaults are tuned for **higher signal quality** (override via `.env`):

| Setting | Default | Effect |
|--------|---------|--------|
| `MIN_VOLUME_USD` | `5000` | Drops thin markets (lower min = more candidates) |
| `MAX_DAYS_TO_EXPIRY` | `60` | Focuses on nearer-term resolution |
| `MIN_IMPLIED_PROB` / `MAX_IMPLIED_PROB` | `0.10` / `0.90` | Ignores extreme longshots / near-certainties |
| `MAX_CANDIDATES_PER_RUN` | `10` | Top volume markets processed per cycle (after filters) |
| `ALERT_MIN_SIGNAL_SCORE` | `7` | Telegram only when model score ≥ 7 |

Stricter volume and expiry mean **fewer** markets pass; tighter implied band removes **very low / very high** prices; more candidates per run only applies to what remains after filters.

## GPT Researcher (optional)

GPT Researcher is **not** installed by this repo. Integration is via **`GPT_RESEARCHER_COMMAND`**: any **executable file** (including the **versioned wrapper** below). There is no requirement for a binary named `gpt-researcher` on `PATH`.

**Layout (sibling folders):** place `gptr-venv` and `gpt-researcher-repo` next to the `trading-ai/` checkout (same parent directory). The in-repo wrapper resolves that parent automatically.

**Recommended (versioned):** use the script tracked in git:

```bash
cd trading-ai
chmod +x scripts/gptr-run.sh
```

Set in `.env` (use **quotes** if the absolute path contains spaces):

`GPT_RESEARCHER_COMMAND="/absolute/path/to/trading-ai/scripts/gptr-run.sh"`

You can copy the absolute path with `pwd`: `echo "\"$(pwd)/scripts/gptr-run.sh\""` from inside `trading-ai`.

- **`GPT_RESEARCHER_ENABLED=false`** (default): no subprocess, **no** GPT Researcher log lines.
- **`GPT_RESEARCHER_ENABLED=true`**: each pipeline run **resolves `GPT_RESEARCHER_COMMAND` once**. It must be **non-empty** and point to a file that **exists** and is **executable**. If that check fails, you get **one warning per run** and the hook is skipped for all candidates. If it succeeds, trading-ai runs that command with the **market query as the final argument**.

Tavily, Firecrawl (when configured), and OpenAI briefs **do not** depend on GPT Researcher.

## Recovery / operations

- **Lost local DB**: delete `data/trading_ai.sqlite`; the next run recreates tables. Historical briefs/alerts are lost unless you restore from backup.
- **Bad Telegram alerts**: raise `ALERT_MIN_SIGNAL_SCORE` in `.env` or disable Telegram by leaving tokens empty.
- **Empty candidate list**: relax filters (`MIN_VOLUME_USD`, `MAX_DAYS_TO_EXPIRY`, `REQUIRE_IMPLIED_PROBABILITY=false`) or increase `MARKETS_FETCH_LIMIT`.
- **API errors**: check keys in `.env`, network, and provider status; logs go to stderr with stack traces on failures.

## Layout

- `src/trading_ai/clients/` — HTTP wrappers for Polymarket, Tavily, Firecrawl
- `src/trading_ai/market/` — filters and candidate selection (uses clients)
- `src/trading_ai/intake/` — enrichment orchestration (Tavily/Firecrawl + optional GPT Researcher hook)
- `src/trading_ai/storage/` — SQLite persistence (markets, briefs, alerts, decisions)
- `src/trading_ai/decisions/` — human decision recording; Phase 3 calibration hooks
- `src/trading_ai/pipeline/` — run orchestration

See the initial design notes for the end-to-end execution flow (trigger → fetch → enrich → AI → alert → storage).

## License

Internal / proprietary unless you add a license file later.
