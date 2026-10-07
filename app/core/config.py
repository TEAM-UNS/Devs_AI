from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore"
    )

    database_url: str
    db_echo: bool
    db_pool_size: int
    db_max_overflow: int

    redis_url: str
    arq_max_jobs: int
    arq_job_timeout: int

    google_api_key: str
    typesafe_api_key: str
    gemini_embed_model: str

    embed_provider: Literal["ollama", "gemini", "fake"] = "ollama"
    ollama_host: str
    embed_ollama_model: str

    gemini_model: str
    llm_max_retry: int = 3

    chat_rate_limit_per_minute: int
    chat_daily_token_budget: int

    embed_dim: int = 1024
    embed_batch_size: int = 96
    embed_max_retry: int = 1
    embed_backfill_limit: int = 500

    embed_rpm: int = 0
    embed_tpm: int = 0

    crawl_delay_seconds: float = 1.0
    crawl_max_delay_seconds: float = 20.0
    crawl_delay_factor: float = 1.6
    crawl_max_retry: int = 3
    crawl_skip_seen_days: int = 7
    crawl_user_agent: str = "jobstack-bot/0.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()