"""★ arq 태스크 — worker.py 의 WorkerSettings.functions 에 등록된다.

    crawl_dispatch()            04:00 cron. 사이트×키워드 조합으로 팬아웃
                                _job_id = f"crawl:{site}:{keyword}:{date}" 로 중복 차단
    crawl_site(site, keyword)   수집 → upsert. 재시도 3
                                ★ 임베딩을 걸지 않는다. 적재만 하고, 임베딩은
                                  나중에 로컬 모델로 한 번에 돌린다
                                  (python -m app.cli embed)
    embed_postings(ids)         청크 분할 → 배치 임베딩 → upsert. 재시도 3
    embed_backfill()            누락·실패분. cron 없음 — 수동 실행 전용
    embed_companies()           기업 설명 변경분. cron 없음 — 수동 실행 전용

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

from app.core.config import get_settings
from app.core.redis import crawl_job_id
from app.domains.crawler import embed_service
from app.domains.crawler.config import (
    DEFAULT_SKIP_SEEN_DAYS,
    build_crawler,
    iter_crawl_jobs,
)
from app.domains.crawler.service import CrawlService
from app.domains.market import enums, repository
from app.llm.port import EmbedderPort

log = logging.getLogger(__name__)


def _sessionmaker(ctx: dict[str, Any]) -> embed_service.SessionFactory:
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


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
async def crawl_dispatch(ctx: dict[str, Any]) -> dict[str, Any]:
    redis = ctx["redis"]
    # ★ 로컬 날짜다. arq cron 은 로컬 시각 04:00 에 뜨는데 여기서 UTC 날짜를
    day = datetime.now().astimezone().strftime("%Y%m%d")

    enqueued = 0
    duplicated = 0
    for job in iter_crawl_jobs():
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
# ═══════════════════════════════════════════════════════════════════════════
async def embed_postings(ctx: dict[str, Any], posting_ids: list[int]) -> dict[str, Any]:
    return await _run_embed(
        ctx,
        source="postings",
        runner=lambda factory, embedder: embed_service.embed_postings(
            factory, embedder, posting_ids=posting_ids
        ),
    )


async def embed_backfill(ctx: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    cap = limit or get_settings().embed_backfill_limit
    return await _run_embed(
        ctx,
        source="backfill",
        runner=lambda factory, embedder: embed_service.embed_postings(factory, embedder, limit=cap),
    )


async def embed_companies(ctx: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
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
