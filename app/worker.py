import logging
from typing import Any, ClassVar

from arq import cron
from arq.worker import func

from app.core.config import get_settings
from app.core.log import setup_logging
from app.core.database import close_engine, engine, session_factory
from app.core.redis import redis_settings
from app.domains.crawler.tasks import (
    crawl_dispatch,
    crawl_site,
    embed_backfill,
    embed_companies,
    embed_postings,
)
from app.domains.crawler import repository
from app.infra.embedding.factory import build_embedder

log = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]):
    setup_logging()
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


async def shutdown(ctx: dict[str, Any]):
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
