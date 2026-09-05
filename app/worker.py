"""arq WorkerSettings · cron 정의.

    uv run arq app.worker.WorkerSettings

등록 태스크 (crawler/tasks.py 에 구현)
    crawl_dispatch    04:00  사이트×키워드 팬아웃
    crawl_site        (팬아웃 대상)
    embed_postings    (crawl_site 가 enqueue)
    embed_backfill    05:30
    embed_companies   06:00

설정
    max_jobs=4        임베딩 API 동시 호출 제한 고려
    job_timeout=600
    중복 방지         _job_id = f"crawl:{site}:{keyword}:{date}"
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from arq import cron
from arq.worker import func

from app.core.config import get_settings
from app.core.database import close_engine, engine, session_factory
from app.core.redis import redis_settings
from app.domains.crawler.tasks import (
    crawl_dispatch,
    crawl_site,
    embed_backfill,
    embed_companies,
    embed_postings,
)
from app.domains.market import repository
from app.llm.embed_adapter import build_embedder

log = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()

    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["sessionmaker"] = session_factory
    ctx["embedder"] = build_embedder()

    embedder = ctx["embedder"]
    log.info(
        "worker 기동 — embedder=%s model=%s dim=%d",
        type(embedder).__name__,
        getattr(embedder, "model", "-"),
        settings.embed_dim,
    )

    async with ctx["sessionmaker"]() as session:
        stale = await repository.fail_stale_runs(
            session, older_than_seconds=settings.arq_job_timeout * 2
        )
        await session.commit()
    if stale:
        log.warning("이전 실행에서 마감되지 못한 crawl_run %d건을 failed 로 정리했습니다.", stale)


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_engine()
    log.info("worker 종료 — 커넥션 풀 정리 완료")


class WorkerSettings:
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = get_settings().arq_max_jobs
    job_timeout = get_settings().arq_job_timeout

    keep_result = 86400
    max_tries = 3

    functions: ClassVar[list] = [
        func(crawl_dispatch, name="crawl_dispatch", max_tries=1),
        func(crawl_site, name="crawl_site", max_tries=3),
        func(embed_postings, name="embed_postings", max_tries=3),
        func(embed_backfill, name="embed_backfill", max_tries=2),
        func(embed_companies, name="embed_companies", max_tries=2),
    ]

    cron_jobs: ClassVar[list] = [
        cron(crawl_dispatch, hour=8, minute=30, run_at_startup=True, max_tries=1),
    ]
