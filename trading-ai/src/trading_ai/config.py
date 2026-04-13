from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` from project root (parent of `src/`) so runtime and CLI match regardless of CWD.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    data_dir: Path = Field(default=Path("data"))

    # Polymarket (Gamma API — public)
    polymarket_gamma_base: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Gamma API base URL",
    )
    polymarket_markets_path: str = "/markets"

    # Market filters (Phase 1 defaults tuned for signal quality; override via .env)
    min_volume_usd: float = Field(default=5_000.0, ge=0)
    max_days_to_expiry: Optional[float] = Field(
        default=60.0,
        description="Exclude markets resolving after this many days; null = no max",
    )
    min_implied_prob: float = Field(default=0.10, ge=0.0, le=1.0)
    max_implied_prob: float = Field(default=0.90, ge=0.0, le=1.0)
    require_implied_probability: bool = Field(
        default=True,
        description="If true, drop markets where implied probability cannot be parsed",
    )
    markets_fetch_limit: int = Field(default=200, ge=1, le=500)
    max_candidates_per_run: int = Field(default=10, ge=1, le=50)

    # Tavily
    tavily_api_key: Optional[str] = None
    tavily_base: str = "https://api.tavily.com"

    # Firecrawl
    firecrawl_api_key: Optional[str] = None
    firecrawl_base: str = "https://api.firecrawl.dev"

    # OpenAI (trade briefs)
    openai_api_key: Optional[str] = None
    openai_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # GPT Researcher: optional external process (shell wrapper or any executable). See README.
    gpt_researcher_enabled: bool = Field(
        default=False,
        description="Master switch; when false, no GPT Researcher logs or subprocesses",
    )
    gpt_researcher_command: str = Field(
        default="",
        description="Executable or wrapper script path (shlex); query is passed as the last argument",
    )

    # Alerts
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    alert_min_signal_score: int = Field(
        default=7,
        ge=1,
        le=10,
        description="Only send Telegram when brief.signal_score >= this",
    )

    # Scheduler (optional — empty disables daemon schedule)
    schedule_interval_minutes: Optional[int] = Field(default=None, ge=1)


def get_settings() -> Settings:
    return Settings()
