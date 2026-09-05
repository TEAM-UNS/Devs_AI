"""임베딩 파이프라인 전체 — FakeEmbedder 로 API 키 없이 돌린다.

실 postgres+pgvector 를 쓴다. vector 컬럼과 부분 인덱스는 sqlite 로 대체
검증이 불가능하다. 접속이 안 되면 conftest 의 db 픽스처가 skip 한다.

여기서 못 박는 것
    - 청크 생성 · 섹션 분포
    - **배치**: 공고 N건이 API 1회로 묶인다 (공고당 1회면 안 된다)
    - **멱등**: 같은 명령 두 번째는 0건 · API 0회
    - 본문 변경 시 바뀐 청크만 재임베딩 (chunk_hash)
    - 본문이 짧아지면 남은 청크 삭제
    - body_is_image · description IS NULL · body_extract_failed 제외
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.core.database import get_worker_session
from app.domains.crawler import embed_service
from app.domains.market import repository

BODY_V1 = """[주요업무]
- 결제 서버 API 개발 및 운영

[자격요건]
- Python 3년 이상

[우대사항]
- Kubernetes 운영 경험
"""

# 자격요건만 바꾼 버전. responsibility · preferred 청크는 그대로여야 한다.
BODY_V2 = """[주요업무]
- 결제 서버 API 개발 및 운영

[자격요건]
- Go 5년 이상

[우대사항]
- Kubernetes 운영 경험
"""

# 섹션이 하나만 남은 버전. 나머지 청크는 삭제되어야 한다.
BODY_SHRUNK = """[주요업무]
- 결제 서버 API 개발 및 운영
"""


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class _Fixture:
    """테스트가 만든 공고 id 들. 끝나면 전부 지운다."""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:12]
        self.ids: list[int] = []


@pytest.fixture
async def postings(db) -> AsyncIterator[_Fixture]:
    """테스트 전용 공고를 심고, 끝나면 지운다.

    실 데이터와 섞이지 않도록 source_job_id 에 uuid 를 박는다.
    """
    fixture = _Fixture()
    yield fixture

    if fixture.ids:
        async with get_worker_session() as session:
            await session.exec(
                text("DELETE FROM market.job_posting WHERE id = ANY(:ids)").bindparams(
                    ids=fixture.ids
                )
            )


async def _insert(fixture: _Fixture, *, body: str | None, **overrides) -> int:
    values = {
        "source": "saramin",
        "source_job_id": f"test-{fixture.tag}-{len(fixture.ids)}",
        "title": "테스트 공고",
        "description": body,
        "content_hash": _hash(body or ""),
        "body_is_image": False,
        "body_extract_failed": False,
        **overrides,
    }
    async with get_worker_session() as session:
        posting_id = await repository.upsert_posting(session, values)
    fixture.ids.append(posting_id)
    return posting_id


async def _chunks(posting_id: int) -> list[tuple[str, int, str]]:
    async with get_worker_session() as session:
        rows = (
            await session.exec(
                text(
                    "SELECT section, seq, chunk_hash FROM market.posting_chunk "
                    "WHERE posting_id = :pid ORDER BY section, seq"
                ).bindparams(pid=posting_id)
            )
        ).all()
    return [(s, q, h) for s, q, h in rows]


async def _embed_hash(posting_id: int) -> str | None:
    async with get_worker_session() as session:
        row = (
            await session.exec(
                text("SELECT embed_hash FROM market.job_posting WHERE id = :pid").bindparams(
                    pid=posting_id
                )
            )
        ).one()
    return row[0]


async def _vector_is_set(posting_id: int) -> bool:
    async with get_worker_session() as session:
        row = (
            await session.exec(
                text(
                    "SELECT COUNT(*) FILTER (WHERE embedding IS NULL) "
                    "FROM market.posting_chunk WHERE posting_id = :pid"
                ).bindparams(pid=posting_id)
            )
        ).one()
    return row[0] == 0


# ═══════════════════════════════════════════════════════════════════════════
async def test_full_pipeline_with_fake_embedder(postings, fake_embedder) -> None:
    """청크 생성 → 임베딩 → 저장 → embed_hash 마감."""
    posting_id = await _insert(postings, body=BODY_V1)

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.targets == 1
    assert stats.postings == 1
    assert stats.chunks_written == 3
    assert stats.errors == 0

    chunks = await _chunks(posting_id)
    assert [c[0] for c in chunks] == ["preferred", "required", "responsibility"]
    assert await _vector_is_set(posting_id)
    assert await _embed_hash(posting_id) == _hash(BODY_V1)


async def test_second_run_is_a_no_op(postings, fake_embedder) -> None:
    """★ 같은 명령을 두 번 실행하면 두 번째는 0건 · API 0회."""
    posting_id = await _insert(postings, body=BODY_V1)
    await embed_service.embed_postings(get_worker_session, fake_embedder, posting_ids=[posting_id])
    calls_after_first = fake_embedder.call_count

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.targets == 0
    assert stats.chunks_written == 0
    assert stats.api_calls == 0
    assert fake_embedder.call_count == calls_after_first


async def test_many_postings_share_one_api_call(postings, fake_embedder) -> None:
    """★ 배치. 공고 1건당 API 1회를 부르는 구조면 안 된다.

    공고 10건 × 3청크 = 30청크 → 96개 상한 안이므로 호출은 1회여야 한다.
    """
    ids = [await _insert(postings, body=BODY_V1 + f"\n- 항목 {i}") for i in range(10)]

    stats = await embed_service.embed_postings(get_worker_session, fake_embedder, posting_ids=ids)

    assert stats.targets == 10
    assert stats.postings == 10
    assert stats.chunks_written == 30
    assert fake_embedder.call_count == 1, "공고마다 따로 부르고 있다"
    assert stats.api_calls == 1


async def test_batch_size_is_respected(postings, fake_embedder) -> None:
    """96개를 넘으면 호출이 나뉜다. 40공고 × 3청크 = 120청크 → 2회."""
    ids = [await _insert(postings, body=BODY_V1 + f"\n- 항목 {i}") for i in range(40)]

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=ids, batch_size=96
    )

    assert stats.chunks_written == 120
    assert fake_embedder.call_count == 2


async def test_only_changed_chunk_is_re_embedded(postings, fake_embedder) -> None:
    """자격요건만 바뀌면 청크 1개만 다시 부른다 (chunk_hash 비교)."""
    posting_id = await _insert(postings, body=BODY_V1)
    await embed_service.embed_postings(get_worker_session, fake_embedder, posting_ids=[posting_id])
    before = await _chunks(posting_id)

    async with get_worker_session() as session:
        await session.exec(
            text(
                "UPDATE market.job_posting SET description = :body, content_hash = :h "
                "WHERE id = :pid"
            ).bindparams(body=BODY_V2, h=_hash(BODY_V2), pid=posting_id)
        )

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.chunks_written == 1, "바뀌지 않은 청크까지 다시 임베딩했다"
    assert stats.chunks_reused == 2

    after = {(s, q): h for s, q, h in await _chunks(posting_id)}
    old = {(s, q): h for s, q, h in before}
    assert after[("required", 0)] != old[("required", 0)]
    assert after[("responsibility", 0)] == old[("responsibility", 0)]
    assert after[("preferred", 0)] == old[("preferred", 0)]


async def test_shrunk_body_deletes_stale_chunks(postings, fake_embedder) -> None:
    """본문이 짧아지면 남은 청크를 지운다.

    안 지우면 원문에 없는 문장이 검색 근거로 계속 인용된다.
    """
    posting_id = await _insert(postings, body=BODY_V1)
    await embed_service.embed_postings(get_worker_session, fake_embedder, posting_ids=[posting_id])
    assert len(await _chunks(posting_id)) == 3

    async with get_worker_session() as session:
        await session.exec(
            text(
                "UPDATE market.job_posting SET description = :body, content_hash = :h "
                "WHERE id = :pid"
            ).bindparams(body=BODY_SHRUNK, h=_hash(BODY_SHRUNK), pid=posting_id)
        )

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.chunks_deleted == 2
    assert [c[0] for c in await _chunks(posting_id)] == ["responsibility"]


# ── 제외 대상 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("body", "overrides"),
    [
        (None, {}),
        (BODY_V1, {"body_is_image": True}),
        (BODY_V1, {"body_extract_failed": True}),
    ],
    ids=["description_null", "body_is_image", "body_extract_failed"],
)
async def test_excluded_postings_are_never_targets(
    postings, fake_embedder, body, overrides
) -> None:
    posting_id = await _insert(postings, body=body, **overrides)

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.targets == 0
    assert fake_embedder.call_count == 0
    assert await _chunks(posting_id) == []


async def test_body_without_embeddable_section_closes_hash(postings, fake_embedder) -> None:
    """복지만 있는 본문은 청크가 0개다. 백필이 매일 다시 집지 않게 닫는다."""
    posting_id = await _insert(postings, body="복리후생\n- 점심 제공\n- 야근 없음")

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )

    assert stats.targets == 1
    assert stats.empty == 1
    assert stats.chunks_written == 0
    assert await _embed_hash(posting_id) is not None

    again = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=[posting_id]
    )
    assert again.targets == 0
