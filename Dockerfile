# Python 3.11+ required for py-clob-client (Polymarket CLOB)
FROM python:3.11-slim

WORKDIR /app

COPY trading-ai/requirements.txt .
RUN python3.11 -m pip install -r requirements.txt

COPY trading-ai/ ./

ENV PYTHONPATH=src
ENV EZRAS_RUNTIME_ROOT=/app/ezras-runtime
RUN mkdir -p /app/ezras-runtime/shark/state
RUN mkdir -p /app/ezras-runtime/shark/logs
RUN mkdir -p /app/ezras-runtime/shark/state/backups

CMD ["python3.11", "-m", "trading_ai.shark.run_shark"]
