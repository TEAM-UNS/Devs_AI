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
    # ★ GOOGLE_API_KEY 는 챗봇(gemini)과 임베딩이 함께 쓰는 하나의 키다.
    #   따로 발급하지 않는다.
    #   지금 이 키를 실제로 쓰는 것은 임베딩뿐이다 — chat_adapter.py 는 아직
    #   구현체가 없는 껍데기라 LLM_MODEL 기본값도 손대지 않았다.
    google_api_key: str = ""
    llm_model: str = "claude-opus-5"
    llm_max_tokens: int = 16000
    use_fake_llm: bool = False

    # ── embedding ───────────────────────────────────────────────────────────
    # 어느 구현을 쓸지. build_embedder() 가 이 값 하나로 분기한다.
    #     auto   키가 있으면 gemini, 없으면 fake (기본값)
    #     gemini 임베딩 API (gemini-embedding-2). 키는 GOOGLE_API_KEY 공용
    #     fake   해시 기반 더미 벡터
    #
    # ★ 모델을 바꾸면 기존 벡터는 못 쓴다. 차원만 1024 로 맞으면 INSERT 는
    #   통과하는데 코사인 유사도만 조용히 깨진다. posting_chunk.embedding 과
    #   company.embedding 을 전량 재생성해야 한다.
    embed_provider: Literal["auto", "gemini", "fake"] = "auto"

    gemini_embed_model: str = "gemini-embedding-2"
    embed_dim: int = EMBEDDING_DIM
    # gemini 의 batchEmbedContents 는 한 요청에 최대 100개다(실측: 101개는 400).
    # 96 은 그 아래라 그대로 쓴다. 어댑터가 100 으로 한 번 더 자른다.
    embed_batch_size: int = 96
    embed_max_retry: int = 3
    embed_backfill_limit: int = 500
    # crawl_site 가 embed_postings 를 enqueue 할 때 한 job 에 넣는 공고 수.
    # 0 이면 쪼개지 않고 한 번에 넘긴다.
    embed_enqueue_chunk: int = 0

    # 계정 레이트리밋. 0 이면 클라이언트에서 제한하지 않는다.
    #
    # gemini 선결제 등급은 한도가 충분히 위에 있어 기본은 0(끔) 이다. 끄면
    # 배치는 개수 상한(96)만 보고 _RateLimiter 는 통째로 no-op 이 된다.
    # 429 가 실제로 보이기 시작하면 그때 실측값을 넣는다.
    embed_rpm: int = 0
    embed_tpm: int = 0

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
    @staticmethod
    def _parse_comma_list(raw_string: str) -> list[str]:
        return [item.strip() for item in raw_string.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return self._parse_comma_list(self.cors_origins)

    @property
    def crawl_keyword_list(self) -> list[str]:
        return self._parse_comma_list(self.crawl_keywords)

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
