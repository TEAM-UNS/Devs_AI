"""async engine · sessionmaker · get_session.

- 드라이버: postgresql+psycopg (SQLAlchemy 2.x async)
- 메타데이터: SQLModel.metadata (모델은 SQLModel table=True)
- 엔진은 지연 생성 싱글턴이다. import 시점에 접속하지 않는다(테스트 격리).
- 접속을 두 개로 나눌 수 있다
    기본(get_session)      크롤러/임베딩 — market 쓰기 권한 필요
    챗봇(get_chat_session) CHAT_DATABASE_URL 이 있으면 별도 롤(ai_chat)로 접속
  운영에서 분리하면 R3(챗봇은 market 읽기만)이 DB 권한으로 강제된다.

★ Windows: psycopg 는 기본 ProactorEventLoop 에서 async 모드로 동작하지
  않는다. 프로세스 진입점(main.py · worker.py)이 실행 전에
  ensure_selector_event_loop_policy() 를 호출해야 한다.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings

# 모델이 등록되는 메타데이터. alembic 의 target_metadata 도 이것을 본다.
metadata = SQLModel.metadata

_engines: dict[str, AsyncEngine] = {}
_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}

DEFAULT = "default"
CHAT = "chat"


def ensure_selector_event_loop_policy() -> None:
    """Windows 에서 psycopg async 가 동작하도록 이벤트 루프 정책을 바꾼다.

    루프가 이미 돌고 있으면 정책 변경이 소용없으므로, 반드시 uvicorn/arq 가
    루프를 만들기 전에 호출해야 한다.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _create_engine(url: str) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # 유휴 커넥션이 끊긴 경우 재연결
    )


def get_engine(name: str = DEFAULT) -> AsyncEngine:
    """이름별 엔진 싱글턴. name 은 DEFAULT · CHAT."""
    if name not in _engines:
        settings = get_settings()
        url = settings.effective_chat_database_url if name == CHAT else settings.database_url
        _engines[name] = _create_engine(url)
    return _engines[name]


def get_sessionmaker(name: str = DEFAULT) -> async_sessionmaker[AsyncSession]:
    if name not in _sessionmakers:
        _sessionmakers[name] = async_sessionmaker(
            get_engine(name),
            class_=AsyncSession,
            expire_on_commit=False,  # 커밋 후에도 응답 직렬화에 쓸 수 있게
            autoflush=False,
        )
    return _sessionmakers[name]


# ── FastAPI 의존성 ──────────────────────────────────────────────────────────
async def get_session() -> AsyncIterator[AsyncSession]:
    """요청당 세션 1개. 커밋은 호출부(서비스) 책임, 롤백만 여기서 보장한다."""
    async with get_sessionmaker(DEFAULT)() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_chat_session() -> AsyncIterator[AsyncSession]:
    """챗봇용 세션. CHAT_DATABASE_URL 이 있으면 읽기 전용 롤로 접속한다."""
    async with get_sessionmaker(CHAT)() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── 워커(arq) · 스크립트용 ──────────────────────────────────────────────────
@asynccontextmanager
async def session_scope(name: str = DEFAULT) -> AsyncIterator[AsyncSession]:
    """요청 컨텍스트가 없는 곳에서 쓰는 세션. 정상 종료 시 커밋한다."""
    async with get_sessionmaker(name)() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── lifespan ────────────────────────────────────────────────────────────────
async def dispose_engines() -> None:
    """앱/워커 종료 시 커넥션 풀 정리."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()
    _sessionmakers.clear()
