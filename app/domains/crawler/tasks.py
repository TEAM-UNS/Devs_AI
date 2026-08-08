"""★ arq 태스크 — worker.py 의 WorkerSettings.functions 에 등록된다.

    crawl_dispatch()            04:00 cron. 사이트×키워드 조합으로 팬아웃
                                _job_id = f"crawl:{site}:{keyword}:{date}" 로 중복 차단
    crawl_site(site, keyword)   수집 → upsert → 변경분 embed_postings enqueue
                                재시도 3
    embed_postings(ids)         청크 분할 → 배치 임베딩 → upsert. 재시도 3
    embed_backfill()            05:30. 누락·실패분 최대 500건. 재시도 2
    embed_companies()           06:00. 기업 설명 변경분. 재시도 2

공통
    - 시작 시 crawl_run(status=running) 기록, 종료 시 success/partial/failed 마감
      (수집은 CrawlService 가, 임베딩은 여기서 직접 기록한다)
    - job_timeout=600, max_jobs=4

★ FastAPI 의 Depends 는 여기서 동작하지 않는다.
  engine · sessionmaker · embedder 는 worker.on_startup 이 ctx 에 넣어 둔 것을
  꺼내 쓴다. 태스크 안에서 새로 만들면 커넥션 풀이 태스크 수만큼 생긴다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core import enums
from app.core.config import get_settings
from app.core.redis import crawl_job_id
from app.domains.crawler import embed_service
from app.domains.crawler.config import (
    DEFAULT_SKIP_SEEN_DAYS,
    build_crawler,
    iter_crawl_jobs,
)
from app.domains.crawler.service import CrawlService
from app.domains.market import repository
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)


def _sessionmaker(ctx: dict[str, Any]) -> embed_service.SessionFactory:
    """on_startup 이 넣어 둔 sessionmaker. 없으면 즉시 실패시킨다."""
    factory = ctx.get("sessionmaker")
    if factory is None:
        raise RuntimeError(
            "ctx['sessionmaker'] 가 없습니다. worker.on_startup 이 실행되지 않았습니다."
        )
    return factory


def _embedder(ctx: dict[str, Any]) -> EmbedderPort:
    embedder = ctx.get("embedder")
    if embedder is None:
        raise RuntimeError("ctx['embedder'] 가 없습니다. worker.on_startup 을 확인하세요.")
    return embedder


def _chunked(items: list[int], size: int) -> list[list[int]]:
    """size 개씩 자른다. size<=0 이면 통째로 하나."""
    if size <= 0 or len(items) <= size:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


# ═══════════════════════════════════════════════════════════════════════════
#  수집
# ═══════════════════════════════════════════════════════════════════════════
async def crawl_dispatch(ctx: dict[str, Any]) -> dict[str, Any]:
    """04:00 — CRAWL_CONFIG 대로 crawl_site 를 팬아웃한다.

    ★ enqueue 하고 즉시 끝난다. "오늘 수집 전체 완료" 는 추적하지 않는다
      (명세 3-4). 05:30 백필이 누락분을 청소해 결과적 정합성을 보장한다.
      batch_id + Redis 카운터 같은 구조를 만들지 않는다.
    """
    redis = ctx["redis"]
    # ★ 로컬 날짜다. arq cron 은 로컬 시각 04:00 에 뜨는데 여기서 UTC 날짜를
    #   쓰면 KST 04:00 = 전날 19:00 UTC 라 날짜 경계가 어긋난다. 그러면 같은
    #   날 낮에 수동으로 다시 돌렸을 때 중복 차단이 안 걸린다.
    day = datetime.now().astimezone().strftime("%Y%m%d")

    enqueued = 0
    duplicated = 0
    for job in iter_crawl_jobs():
        # 같은 날 같은 (사이트, 키워드) 는 한 번만. 이미 큐에 있으면
        # enqueue_job 이 None 을 돌려준다.
        result = await redis.enqueue_job(
            "crawl_site",
            job.site,
            job.keyword,
            job.pages,
            DEFAULT_SKIP_SEEN_DAYS,
            _job_id=crawl_job_id(job.site, job.job_key, day),
        )
        if result is None:
            duplicated += 1
            log.info("중복 큐잉 차단: %s/%s", job.site, job.job_key)
        else:
            enqueued += 1

    log.info("crawl_dispatch: %d건 enqueue (중복 차단 %d)", enqueued, duplicated)
    return {"enqueued": enqueued, "duplicated": duplicated}


async def crawl_site(
    ctx: dict[str, Any],
    site: str,
    keyword: str | None = None,
    pages: int = 1,
    skip_seen_days: int = DEFAULT_SKIP_SEEN_DAYS,
) -> dict[str, Any]:
    """한 사이트(+키워드) 수집 → upsert → 변경분 embed_postings enqueue.

    1페이지 0건이면 SelectorBrokenError 가 올라와 태스크가 실패하고
    crawl_run 은 status='failed' 로 닫힌다 (명세 3-3).
    """
    log.info(
        "crawl_site 시작: site=%s keyword=%s pages=%d skip_seen_days=%d",
        site,
        keyword,
        pages,
        skip_seen_days,
    )
    started = datetime.now(UTC)

    async with build_crawler(site, keyword) as crawler:
        service = CrawlService(crawler)
        stats = await service.crawl(
            pages=pages,
            skip_seen_days=skip_seen_days,
            keyword=keyword,
        )

    # 변경분만 임베딩으로 넘긴다. 해시가 같아 touch 만 한 공고는 재임베딩할
    # 것이 없다.
    #
    # ★ EMBED_ENQUEUE_CHUNK 개씩 쪼개서 여러 job 으로 넘긴다. 사람인 8페이지
    #   전체 재수집이면 변경분이 300건씩 나오는데, 무료 등급(분당 12건)에서
    #   한 job 에 몰아넣으면 job_timeout=600 안에 못 끝나고 통째로 잘린다.
    #   쪼개 두면 각 job 이 시간 안에 끝나고, 하나가 실패해도 그 조각만 다시
    #   돈다. 표준 등급으로 올리면 0 으로 두어 한 번에 넘기면 된다.
    #
    # ★ enqueue 실패로 태스크를 죽이지 않는다. 여기서 예외를 올리면 arq 가
    #   수집 전체를 재시도해 방금 끝낸 수백 건의 요청을 다시 쏜다. 임베딩은
    #   05:30 백필이 주워 가므로 로그만 남기고 넘어가는 쪽이 싸다.
    if stats.changed_posting_ids:
        try:
            for batch in _chunked(stats.changed_posting_ids, get_settings().embed_enqueue_chunk):
                await ctx["redis"].enqueue_job("embed_postings", batch)
        except Exception:
            log.exception(
                "embed_postings enqueue 실패 (%d건) — 05:30 백필로 넘깁니다",
                len(stats.changed_posting_ids),
            )

    elapsed = (datetime.now(UTC) - started).total_seconds()
    log.info("crawl_site 완료: %s/%s — %s (%.1fs)", site, keyword, stats.as_line(), elapsed)
    return {
        "site": site,
        "keyword": keyword,
        "fetched": stats.fetched,
        "inserted": stats.inserted,
        "updated": stats.updated,
        "skipped": stats.skipped,
        "skipped_known": stats.skipped_known,
        "errors": stats.errors,
        "elapsed_sec": round(elapsed, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  임베딩
# ═══════════════════════════════════════════════════════════════════════════
async def embed_postings(ctx: dict[str, Any], posting_ids: list[int]) -> dict[str, Any]:
    """지정 공고를 임베딩한다. 청크 분할 → 배치 임베딩 → upsert.

    멱등하다. 재시도로 같은 id 가 다시 들어와도 embed_hash · chunk_hash 가
    같으면 API 를 부르지 않는다.
    """
    return await _run_embed(
        ctx,
        source="postings",
        runner=lambda factory, embedder: embed_service.embed_postings(
            factory, embedder, posting_ids=posting_ids
        ),
    )


async def embed_backfill(ctx: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """05:30 — 누락·실패분 청소.

    crawl_site 가 enqueue 한 embed_postings 가 죽었거나, 임베딩 API 가
    실패해 embed_hash 를 못 닫은 공고를 여기서 주워 간다.

    한 번에 EMBED_BACKFILL_LIMIT 건만 본다. 남은 것은 다음날 백필이 가져간다.
    """
    cap = limit or get_settings().embed_backfill_limit
    return await _run_embed(
        ctx,
        source="backfill",
        runner=lambda factory, embedder: embed_service.embed_postings(factory, embedder, limit=cap),
    )


async def embed_companies(ctx: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """06:00 — 기업 프로필(설명+사업내용+업종) 변경분 임베딩."""
    return await _run_embed(
        ctx,
        source="companies",
        runner=lambda factory, embedder: embed_service.embed_companies(
            factory, embedder, limit=limit
        ),
    )


async def _run_embed(
    ctx: dict[str, Any],
    *,
    source: str,
    runner: Callable[
        [embed_service.SessionFactory, EmbedderPort], Awaitable[embed_service.EmbedStats]
    ],
) -> dict[str, Any]:
    """임베딩 태스크 공통 — crawl_run 기록 + 실행 + 마감.

    수집과 달리 임베딩은 서비스가 run 을 열지 않으므로 여기서 연다.
    """
    factory = _sessionmaker(ctx)
    embedder = _embedder(ctx)

    async with factory() as session:
        run_id = await repository.start_run(session, kind=enums.RunKind.EMBED, source=source)
        await session.commit()

    try:
        stats = await runner(factory, embedder)
    except Exception as exc:
        async with factory() as session:
            await repository.finish_run(
                session, run_id, status=enums.RunStatus.FAILED, message=repr(exc)[:1000]
            )
            await session.commit()
        raise

    if stats.errors == 0:
        status = enums.RunStatus.SUCCESS
    elif stats.postings or stats.companies:
        status = enums.RunStatus.PARTIAL
    else:
        status = enums.RunStatus.FAILED

    async with factory() as session:
        await repository.finish_run(
            session,
            run_id,
            status=status,
            fetched=stats.targets,
            updated=stats.postings + stats.companies,
            skipped=stats.chunks_reused,
            embedded=stats.chunks_written,
            errors=stats.errors,
            message="; ".join(stats.error_messages[:5]) or None,
        )
        await session.commit()

    log.info("embed(%s) 완료 — %s", source, stats.as_line())
    return {
        "kind": source,
        "targets": stats.targets,
        "postings": stats.postings,
        "companies": stats.companies,
        "chunks_written": stats.chunks_written,
        "chunks_reused": stats.chunks_reused,
        "chunks_deleted": stats.chunks_deleted,
        "api_calls": stats.api_calls,
        "errors": stats.errors,
    }
