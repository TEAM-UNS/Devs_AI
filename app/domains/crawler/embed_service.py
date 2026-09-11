"""임베딩 오케스트레이션 — 청크 · 기업 프로필 · 스킬.

공고 임베딩
    대상: embed_hash IS DISTINCT FROM content_hash
    chunker 로 청크 생성 → chunk_hash 가 바뀐 청크만 EmbedderPort 호출
    배치: EMBED_BATCH_SIZE(96) 개 단위로 API 1회 (태스크당 1~2회)
    성공 시에만 embed_hash = content_hash 로 갱신
    실패: 3회 재시도 후에도 실패하면 embed_hash 를 갱신하지 않는다
          → 다음 백필에서 자연히 재처리된다

기업 프로필 임베딩
    description + business_content + industry 를 합쳐 임베딩
    셋 다 비어 있으면 스킵

스킬 임베딩
    skill.name + aliases. embed_hash 컬럼이 없어 매번 전량 다시 만든다 (200행 규모)

백필
    누락·실패분 최대 EMBED_BACKFILL_LIMIT(500)건/회
    body_is_image=true · description IS NULL 제외

★ 배치 경계가 이 모듈의 존재 이유다.
  공고 1건당 API 1회를 부르면 660건에 660번 호출한다. 여러 공고의 청크를
  하나의 배치(96개)로 모아서 부르고, 배치가 성공한 뒤에 그 배치에 속한
  공고들의 embed_hash 를 닫는다.
"""

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.domains.crawler.chunker import Chunk, build_chunks, normalize
from app.domains.market import repository
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession] | async_sessionmaker[AsyncSession]


def _call_count(embedder: EmbedderPort) -> int:
    return int(getattr(embedder, "call_count", 0))


@dataclass
class EmbedStats:
    postings: int = 0
    targets: int = 0
    chunks_written: int = 0
    chunks_reused: int = 0
    chunks_deleted: int = 0
    companies: int = 0
    skills: int = 0
    api_calls: int = 0
    empty: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        return (
            f"targets={self.targets} postings={self.postings} "
            f"chunks_written={self.chunks_written} chunks_reused={self.chunks_reused} "
            f"chunks_deleted={self.chunks_deleted} api_calls={self.api_calls} "
            f"empty={self.empty} errors={self.errors}"
        )


@dataclass
class _Pending:
    posting_id: int
    chunk: Chunk


@dataclass
class _Work:
    posting_id: int
    content_hash: str
    chunks: list[Chunk]
    pending: list[Chunk]
    reused: int


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
async def embed_postings(
    session_factory: SessionFactory,
    embedder: EmbedderPort,
    *,
    posting_ids: Sequence[int] | None = None,
    limit: int | None = None,
    batch_size: int | None = None,
) -> EmbedStats:
    settings = get_settings()
    size = batch_size or settings.embed_batch_size
    stats = EmbedStats()

    works: list[_Work] = []
    async with session_factory() as session:
        targets = await repository.iter_postings_to_embed(
            session, posting_ids=posting_ids, limit=limit
        )
        stats.targets = len(targets)

        for posting_id, description, content_hash in targets:
            chunks = build_chunks(description)
            if not chunks:
                stats.empty += 1
                works.append(_Work(posting_id, content_hash, chunks=[], pending=[], reused=0))
                continue

            existing = await repository.get_chunk_state(session, posting_id)
            pending = [c for c in chunks if existing.get((c.section.value, c.seq)) != c.chunk_hash]
            works.append(
                _Work(
                    posting_id=posting_id,
                    content_hash=content_hash,
                    chunks=chunks,
                    pending=pending,
                    reused=len(chunks) - len(pending),
                )
            )

    if not works:
        log.info("임베딩 대상 없음")
        return stats

    batch: list[_Work] = []
    pending_count = 0
    for work in works:
        if batch and pending_count + len(work.pending) > size:
            await _flush(session_factory, embedder, batch, stats, size)
            batch, pending_count = [], 0
        batch.append(work)
        pending_count += len(work.pending)

    if batch:
        await _flush(session_factory, embedder, batch, stats, size)

    log.info("임베딩 완료 — %s", stats.as_line())
    return stats


async def _flush(
    session_factory: SessionFactory,
    embedder: EmbedderPort,
    batch: list[_Work],
    stats: EmbedStats,
    size: int,
) -> None:
    pendings = [_Pending(w.posting_id, c) for w in batch for c in w.pending]

    try:
        vectors: list[list[float]] = []
        for start in range(0, len(pendings), size):
            window = pendings[start : start + size]
            before = _call_count(embedder)
            got = await embedder.embed_documents([p.chunk.content for p in window])
            if len(got) != len(window):
                raise RuntimeError(f"임베딩 개수 불일치: {len(got)} != {len(window)}")
            vectors.extend(got)
            stats.api_calls += max(1, _call_count(embedder) - before)
    except Exception as exc:
        log.exception("임베딩 배치 실패 — 공고 %d건을 다음 백필로 넘깁니다", len(batch))
        stats.errors += len(batch)
        stats.error_messages.append(repr(exc))
        return

    by_posting: dict[int, list[tuple[Chunk, list[float]]]] = {}
    for pending, vector in zip(pendings, vectors, strict=True):
        by_posting.setdefault(pending.posting_id, []).append((pending.chunk, vector))

    written = deleted = 0
    async with session_factory() as session:
        try:
            for work in batch:
                for chunk, vector in by_posting.get(work.posting_id, []):
                    await repository.upsert_posting_chunk(
                        session,
                        posting_id=work.posting_id,
                        section=chunk.section.value,
                        seq=chunk.seq,
                        content=chunk.content,
                        chunk_hash=chunk.chunk_hash,
                        embedding=vector,
                        token_count=chunk.token_count,
                    )
                    written += 1

                deleted += await repository.delete_stale_chunks(
                    session,
                    work.posting_id,
                    [(c.section.value, c.seq) for c in work.chunks],
                )
                # ★ 여기까지 왔을 때만 닫는다.
                await repository.set_posting_embed_hash(session, work.posting_id, work.content_hash)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.exception("청크 저장 실패 — 공고 %d건", len(batch))
            stats.errors += len(batch)
            stats.error_messages.append(repr(exc))
            return

    stats.chunks_written += written
    stats.chunks_deleted += deleted
    stats.postings += len(batch)
    stats.chunks_reused += sum(w.reused for w in batch)


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
def build_company_text(
    description: str | None, business_content: str | None, industry: str | None
) -> str:
    parts = [normalize(p) for p in (description, business_content, industry) if p and p.strip()]
    return "\n".join(parts).strip()


def company_embed_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def embed_companies(
    session_factory: SessionFactory,
    embedder: EmbedderPort,
    *,
    company_ids: Sequence[int] | None = None,
    limit: int | None = None,
    batch_size: int | None = None,
) -> EmbedStats:
    settings = get_settings()
    size = batch_size or settings.embed_batch_size
    stats = EmbedStats()

    async with session_factory() as session:
        rows = await repository.iter_companies_to_embed(
            session, company_ids=company_ids, limit=limit
        )

    targets: list[tuple[int, str, str]] = []
    for company_id, description, business, industry, old_hash in rows:
        text = build_company_text(description, business, industry)
        if not text:
            continue
        new_hash = company_embed_hash(text)
        if old_hash == new_hash:
            continue
        targets.append((company_id, text, new_hash))

    stats.targets = len(targets)
    if not targets:
        log.info("기업 임베딩 대상 없음")
        return stats

    for start in range(0, len(targets), size):
        window = targets[start : start + size]
        try:
            vectors = await embedder.embed_documents([t for _, t, _ in window])
            stats.api_calls += 1
        except Exception as exc:
            log.exception("기업 임베딩 실패 — %d건", len(window))
            stats.errors += len(window)
            stats.error_messages.append(repr(exc))
            continue

        async with session_factory() as session:
            for (company_id, _, new_hash), vector in zip(window, vectors, strict=True):
                await repository.set_company_embedding(
                    session, company_id, embedding=vector, embed_hash=new_hash
                )
                stats.companies += 1
            await session.commit()

    log.info(
        "기업 임베딩 완료 — targets=%d companies=%d api_calls=%d errors=%d",
        stats.targets,
        stats.companies,
        stats.api_calls,
        stats.errors,
    )
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
def build_skill_text(name: str, aliases: Sequence[str]) -> str:
    return ", ".join([name, *aliases])


async def embed_skills(
    session_factory: SessionFactory,
    embedder: EmbedderPort,
    *,
    batch_size: int | None = None,
) -> EmbedStats:
    size = batch_size or get_settings().embed_batch_size
    stats = EmbedStats()

    async with session_factory() as session:
        rows = await repository.list_skills_with_aliases(session)
    stats.targets = len(rows)

    for start in range(0, len(rows), size):
        window = rows[start : start + size]
        try:
            vectors = await embedder.embed_documents(
                [build_skill_text(name, aliases) for _, name, aliases in window]
            )
            stats.api_calls += 1
        except Exception as exc:
            log.exception("스킬 임베딩 실패 — %d건", len(window))
            stats.errors += len(window)
            stats.error_messages.append(repr(exc))
            continue

        async with session_factory() as session:
            for (skill_id, _, _), vector in zip(window, vectors, strict=True):
                await repository.set_skill_embedding(session, skill_id, embedding=vector)
                stats.skills += 1
            await session.commit()

    log.info(
        "스킬 임베딩 완료 — targets=%d skills=%d api_calls=%d errors=%d",
        stats.targets,
        stats.skills,
        stats.api_calls,
        stats.errors,
    )
    return stats
