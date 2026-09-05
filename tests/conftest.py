"""공용 픽스처.

settings_override   .env 무시하고 테스트 설정 주입
db                  테스트 DB 세션 (트랜잭션 롤백 격리)
                    pgvector 가 필요하므로 sqlite 로 대체할 수 없다.
                    docker-compose 의 postgres 에 test 스키마를 쓰거나
                    일회용 컨테이너를 띄운다
fake_llm            app.llm.fake.FakeLLM
fake_embedder       app.llm.fake.FakeEmbedder (해시 기반 결정적 벡터)
client              httpx AsyncClient + FastAPI app (의존성 오버라이드)
site_snapshot       크롤러 테스트용 저장된 HTML/JSON 원본

"""

from __future__ import annotations

import pytest

from app.core.database import close_engine, get_worker_session
from app.llm.fake import FakeEmbedder


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    """API 키 없이 쓰는 결정적 임베더. call_count 로 호출 횟수를 검증한다."""
    return FakeEmbedder()


@pytest.fixture
async def db():
    """실 postgres 세션. 접속이 안 되면 해당 테스트를 건너뛴다.

    docker compose 가 안 떠 있는 환경에서도 순수 단위 테스트는 돌아야 한다.
    """
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
