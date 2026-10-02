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
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5

    redis_url: str
    arq_max_jobs: int = 4
    # 원티드가 목록 150페이지를 걷고, 사람인은 키워드당 30페이지다.
    # 평소에는 증분 필터가 상세를 걸러 600초 안에 끝나지만, 페이지 상한을
    # 올린 직후 첫 수집은 신규가 수백 건이라 600초를 넘는다.
    arq_job_timeout: int = 1800

    cors_origins: str

    google_api_key: str
    typesafe_api_key: str
    gemini_embed_model: str

    embed_provider: Literal["ollama", "gemini", "fake"] = "ollama"
    ollama_host: str
    embed_ollama_model: str

    gemini_model: str
    llm_max_retry: int = 3

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