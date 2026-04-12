from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_ai.models.schemas import (
    AlertRecord,
    CandidateMarket,
    DecisionRecord,
    EnrichmentBundle,
    TradeBrief,
)


def _json_dumps(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(mode="json"), default=str)
    return json.dumps(obj, default=str)


@dataclass
class Store:
    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS enrichments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    brief_created_at TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_summary TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    brief_created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    notes TEXT,
                    decided_at TEXT NOT NULL
                );
                """
            )

    def new_run_id(self) -> str:
        return str(uuid4())

    def log_market(self, run_id: str, market: CandidateMarket) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO market_snapshots (run_id, captured_at, market_id, payload) VALUES (?,?,?,?)",
                (run_id, now, market.market_id, _json_dumps(market)),
            )

    def log_enrichment(self, run_id: str, bundle: EnrichmentBundle) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO enrichments (run_id, captured_at, market_id, payload) VALUES (?,?,?,?)",
                (run_id, now, bundle.market_id, _json_dumps(bundle)),
            )

    def log_brief(self, run_id: str, brief: TradeBrief) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO briefs (run_id, created_at, market_id, payload) VALUES (?,?,?,?)",
                (run_id, brief.created_at.isoformat(), brief.market_id, _json_dumps(brief)),
            )

    def log_alert(self, run_id: str, rec: AlertRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO alerts (run_id, market_id, brief_created_at, channel, payload_summary, sent_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    run_id,
                    rec.market_id,
                    rec.brief_created_at.isoformat(),
                    rec.channel,
                    rec.payload_summary,
                    rec.sent_at.isoformat(),
                ),
            )

    def log_decision(self, rec: DecisionRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions (market_id, brief_created_at, action, notes, decided_at)
                   VALUES (?,?,?,?,?)""",
                (
                    rec.market_id,
                    rec.brief_created_at.isoformat(),
                    rec.action,
                    rec.notes,
                    rec.decided_at.isoformat(),
                ),
            )
