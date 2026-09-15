# 임베딩 파이프라인 테스트

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.core.database import get_worker_session
from app.domains.crawler import embed_service
from app.domains.crawler import repository
from typing import Optional

BODY_V1 = """[주요업무]
- 결제 서버 API 개발 및 운영

[자격요건]
- Python 3년 이상

[우대사항]
- Kubernetes 운영 경험
"""

BODY_V2 = """[주요업무]
- 결제 서버 API 개발 및 운영

[자격요건]
- Go 5년 이상

[우대사항]
- Kubernetes 운영 경험
"""

BODY_SHRUNK = """[주요업무]
- 결제 서버 API 개발 및 운영
"""


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class _Fixture:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:12]
        self.ids: list[int] = []


@pytest.fixture
async def postings(db) -> AsyncIterator[_Fixture]:
    fixture = _Fixture()
    yield fixture

    if fixture.ids:
        async with get_worker_session() as session:
            await session.exec(
                text("DELETE FROM market.job_posting WHERE id = ANY(:ids)").bindparams(
                    ids=fixture.ids
                )
            )


async def _insert(fixture: _Fixture, *, body: Optional[str], **overrides) -> int:
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


async def _embed_hash(posting_id: int) -> Optional[str]:
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


async def test_full_pipeline_with_fake_embedder(postings, fake_embedder) -> None:
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
    ids = [await _insert(postings, body=BODY_V1 + f"\n- 항목 {i}") for i in range(10)]

    stats = await embed_service.embed_postings(get_worker_session, fake_embedder, posting_ids=ids)

    assert stats.targets == 10
    assert stats.postings == 10
    assert stats.chunks_written == 30
    assert fake_embedder.call_count == 1, "공고마다 따로 부르고 있다"
    assert stats.api_calls == 1


async def test_batch_size_is_respected(postings, fake_embedder) -> None:
    ids = [await _insert(postings, body=BODY_V1 + f"\n- 항목 {i}") for i in range(40)]

    stats = await embed_service.embed_postings(
        get_worker_session, fake_embedder, posting_ids=ids, batch_size=96
    )

    assert stats.chunks_written == 120
    assert fake_embedder.call_count == 2


async def test_only_changed_chunk_is_re_embedded(postings, fake_embedder) -> None:
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
