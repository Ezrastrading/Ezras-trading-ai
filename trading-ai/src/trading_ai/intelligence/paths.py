"""Filesystem layout for incident/ticket/learning intelligence under ``EZRAS_RUNTIME_ROOT``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _safe_segment(s: str, max_len: int) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in ("_", "-"))[:max_len] or "unknown"


def tickets_root(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "tickets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tickets_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    return tickets_root(runtime_root=runtime_root) / "tickets.jsonl"


def open_tickets_json_path(runtime_root: Optional[Path] = None) -> Path:
    return tickets_root(runtime_root=runtime_root) / "open_tickets.json"


def ticket_summary_txt_path(runtime_root: Optional[Path] = None) -> Path:
    return tickets_root(runtime_root=runtime_root) / "ticket_summary.txt"


def ticket_routing_log_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    return tickets_root(runtime_root=runtime_root) / "ticket_routing_log.jsonl"


def scoped_tickets_root(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = _safe_segment(avenue_id, 32)
    p = tickets_root(runtime_root=runtime_root) / "avenues" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def ticket_ceo_sessions_dir(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review" / "ticket_ceo_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ticket_ceo_daily_rollup_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p / "ticket_ceo_daily_rollup.json"


def ticket_ceo_daily_rollup_txt_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p / "ticket_ceo_daily_rollup.txt"


def daily_learning_session_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p / "daily_learning_session.json"


def daily_learning_session_txt_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review"
    p.mkdir(parents=True, exist_ok=True)
    return p / "daily_learning_session.txt"


def learning_domains_dir(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning" / "domains"
    p.mkdir(parents=True, exist_ok=True)
    return p


def learning_registry_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p / "learning_registry.json"


def learning_change_log_jsonl_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p / "learning_change_log.jsonl"


def learning_snapshots_dir(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning" / "snapshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def what_learned_today_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p / "what_the_system_learned_today.json"


def what_not_to_do_tomorrow_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p / "what_not_to_do_tomorrow.json"


def what_to_test_tomorrow_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning"
    p.mkdir(parents=True, exist_ok=True)
    return p / "what_to_test_tomorrow.json"


def intelligence_governance_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p / "intelligence_governance.json"


def intelligence_capability_maturity_json_path(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p / "intelligence_capability_maturity.json"


def scoped_intelligence_learning_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = _safe_segment(avenue_id, 32)
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning" / "avenues" / safe / "intelligence"
    p.mkdir(parents=True, exist_ok=True)
    return p


def scoped_intelligence_review_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = _safe_segment(avenue_id, 32)
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review" / "avenues" / safe / "intelligence"
    p.mkdir(parents=True, exist_ok=True)
    return p


def scoped_ticket_pointers_json(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    d = scoped_intelligence_review_dir(avenue_id, runtime_root=runtime_root)
    return d / "ticket_layer_pointers.json"
