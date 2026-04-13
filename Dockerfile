FROM python:3.11-slim

WORKDIR /app

COPY trading-ai/requirements.txt .
RUN pip install -r requirements.txt

COPY trading-ai/ ./trading-ai/

WORKDIR /app/trading-ai
ENV PYTHONPATH=src

CMD ["python3", "-m", "trading_ai.shark.run_shark"]
