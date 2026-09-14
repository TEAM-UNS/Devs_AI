# 테스트 공용 픽스처

from __future__ import annotations

import pytest

from app.core.database import close_engine, get_worker_session
from app.llm.embed.fake import FakeEmbedder


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
async def db():
    try:
        async with get_worker_session() as session:
            await session.exec(_ping())
            yield session
    except Exception as exc:  # noqa: BLE001 — 접속 실패는 skip 사유다
        pytest.skip(f"postgres 에 접속할 수 없습니다: {exc}")


def _ping():
    from sqlalchemy import text

    return text("SELECT 1")


@pytest.fixture(scope="session", autouse=True)
async def _close_engine_at_end():
    yield
    await close_engine()
