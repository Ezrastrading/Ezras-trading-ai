# Trading AI (Phase 1)

Independent AI trading partner for prediction markets: monitor Polymarket, enrich with news and sources, produce structured trade briefs, alert on high-signal candidates, and log everything. **Execution is human-confirmed** — no auto-trading.

This project lives only under `trading-ai/` and is not coupled to Nexplora.

## Project separation guarantee

This system is **fully independent** from Nexplora. It does not read, write, import, or depend on the Nexplora codebase, configuration, or databases. All code and data for this trading partner live under `trading-ai/` only.

## Setup

1. **Python 3.9+** (3.11+ recommended)

2. **Create a virtual environment** (recommended):

   ```bash
   cd trading-ai
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
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
