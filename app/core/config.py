from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        extra="ignore"
    )

    # ── db ──
    database_url: str
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ── worker ──
    redis_url: str
    arq_max_jobs: int = 4
    arq_job_timeout: int = 600

    cors_origins: str = ""

    # ── llm · embedding ──
    google_api_key: str = ""
    embed_provider: Literal["auto", "gemini", "fake"] = "auto"
    gemini_embed_model: str = "gemini-embedding-2"

    embed_dim: int = 1024
    embed_batch_size: int = 96
    embed_max_retry: int = 1
    embed_backfill_limit: int = 500

    embed_rpm: int = 0
    embed_tpm: int = 0

    # ── crawler ──
    crawl_delay_seconds: float = 1.0
    crawl_max_delay_seconds: float = 20.0
    crawl_delay_factor: float = 1.6
    crawl_max_retry: int = 3
    crawl_user_agent: str = "jobstack-bot/0.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
