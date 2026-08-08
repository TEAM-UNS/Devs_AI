"""환경설정 (pydantic-settings).

.env 를 읽어 Settings 하나로 노출한다. get_settings() 는 lru_cache 이므로
프로세스당 1회만 파싱된다.

주의: EMBED_DIM 은 init.sql 의 vector(N) · enums.EMBEDDING_DIM 과 반드시
같아야 한다. 어긋나면 INSERT 단계에서야 터지므로 기동 시점에 막는다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import EMBEDDING_DIM


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── db ──────────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://jobstack:jobstack@localhost:5432/jobstack"
    # 운영에서 챗봇만 다른 롤(ai_chat)로 붙일 때 사용. 없으면 database_url 재사용.
    chat_database_url: str | None = None
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ── worker ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    arq_max_jobs: int = 4
    arq_job_timeout: int = 600

    # ── auth ────────────────────────────────────────────────────────────────
    auth_mode: Literal["dev", "jwt"] = "dev"
    dev_user_id: str = "dev-user-1"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_public_key: str = ""
    jwt_public_key_path: str = ""
    jwks_url: str = ""

    # ── cors ────────────────────────────────────────────────────────────────
    # 콤마 구분 문자열. 리스트가 필요하면 cors_origin_list 를 쓴다.
    cors_origins: str = ""

    # ── llm ─────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 16000
    use_fake_llm: bool = False

    # ── embedding ───────────────────────────────────────────────────────────
    voyage_api_key: str = ""
    embed_model: str = "voyage-3-large"
    embed_dim: int = EMBEDDING_DIM
    embed_batch_size: int = 96
    embed_max_retry: int = 3
    embed_backfill_limit: int = 100
    # crawl_site 가 embed_postings 를 enqueue 할 때 한 job 에 넣는 공고 수.
    # 0 이면 쪼개지 않고 한 번에 넘긴다.
    embed_enqueue_chunk: int = 40
    # 계정 레이트리밋. 0 이면 클라이언트에서 제한하지 않는다.
    # Voyage 무료 등급은 3 RPM · 10K TPM 이라 96개 배치가 그대로 튕긴다.
    # 결제수단을 등록하면 표준 등급으로 올라가므로 그때 0 으로 되돌린다.
    embed_rpm: int = 0
    embed_tpm: int = 0

    # ── 배치 크기 되돌리기 ──────────────────────────────────────────────────
    # 무료 등급(3 RPM · 10K TPM)에서는 분당 12건쯤 처리된다. 그 속도로는
    # 500건 백필이 job_timeout=600 안에 못 끝나 태스크가 통째로 잘린다.
    # 그래서 백필 100건 · enqueue 40건으로 낮춰 뒀다.
    #
    # 표준 등급으로 올린 뒤에는 .env 에서 이렇게 되돌린다.
    #     EMBED_RPM=0
    #     EMBED_TPM=0
    #     EMBED_BACKFILL_LIMIT=500
    #     EMBED_ENQUEUE_CHUNK=0

    # ── chat guard ──────────────────────────────────────────────────────────
    chat_recursion_limit: int = 8
    chat_rate_limit_per_min: int = 20
    chat_daily_token_budget: int = 200_000
    chat_history_token_budget: int = 12_000

    # ── crawler ─────────────────────────────────────────────────────────────
    crawl_delay_seconds: float = 1.0
    crawl_max_delay_seconds: float = 20.0
    crawl_delay_factor: float = 1.6
    crawl_max_retry: int = 3
    crawl_snapshot_dir: str = "./var/snapshots"
    crawl_user_agent: str = "jobstack-bot/0.1"
    crawl_keywords: str = ""

    # ── 파생 값 ─────────────────────────────────────────────────────────────
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def crawl_keyword_list(self) -> list[str]:
        return [k.strip() for k in self.crawl_keywords.split(",") if k.strip()]

    @property
    def effective_chat_database_url(self) -> str:
        """챗봇용 접속 URL. 미설정이면 기본 URL 을 그대로 쓴다(개발)."""
        return self.chat_database_url or self.database_url

    @property
    def is_dev_auth(self) -> bool:
        return self.auth_mode == "dev"

    # ── 검증 ────────────────────────────────────────────────────────────────
    @model_validator(mode="after")
    def _check_embed_dim(self) -> Settings:
        if self.embed_dim != EMBEDDING_DIM:
            raise ValueError(
                f"EMBED_DIM={self.embed_dim} 이지만 스키마는 vector({EMBEDDING_DIM}) 입니다. "
                "차원을 바꾸려면 컬럼 타입 변경 + 전체 재임베딩이 필요합니다."
            )
        return self

    @model_validator(mode="after")
    def _check_jwt_key(self) -> Settings:
        if self.auth_mode == "jwt" and not (
            self.jwt_public_key or self.jwt_public_key_path or self.jwks_url
        ):
            raise ValueError(
                "AUTH_MODE=jwt 인데 공개키가 없습니다. "
                "JWT_PUBLIC_KEY · JWT_PUBLIC_KEY_PATH · JWKS_URL 중 하나를 설정하세요."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
