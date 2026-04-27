# Python 3.11+ required for py-clob-client (Polymarket CLOB)
FROM python:3.11-slim

WORKDIR /app

COPY trading-ai/requirements.txt ./requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

COPY trading-ai/src ./src

ENV PYTHONPATH=/app/src
ENV EZRAS_RUNTIME_ROOT=/app/ezras-runtime
RUN mkdir -p /app/ezras-runtime/shark/state
RUN mkdir -p /app/ezras-runtime/shark/logs
RUN mkdir -p /app/ezras-runtime/shark/state/backups

# Hard build check: verify NTE data import works with PYTHONPATH
RUN PYTHONPATH=/app/src python3.11 -c "import sys; print(sys.path); import trading_ai; import trading_ai.nte.data; print('NTE_DATA_IMPORT_OK')"

CMD ["python3.11", "-m", "trading_ai.shark.run_shark"]
