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

  Windows: 이 모듈 최상단에서 WindowsSelectorEventLoopPolicy 를 설정해야
  psycopg async 가 동작한다.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from arq import cron
from arq.worker import func

from app.core.database import (
    dispose_engines,
    ensure_selector_event_loop_policy,
    get_engine,
    get_sessionmaker,
)

# arq 가 이벤트 루프를 만들기 전에 호출해야 한다. import 시점이 유일한 기회다.
ensure_selector_event_loop_policy()

from app.core.config import get_settings
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
    """엔진 · 세션팩토리 · 임베더를 1회 만들어 ctx 에 심는다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()

    ctx["settings"] = settings
    ctx["engine"] = get_engine()
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["embedder"] = build_embedder()

    # 모델 이름은 settings 가 아니라 어댑터 인스턴스에서 읽는다. FakeEmbedder
    # 로 떨어졌는데 로그에는 실제 모델명이 찍히는 상황을 만들지 않기 위함이다.
    embedder = ctx["embedder"]
    log.info(
        "worker 기동 — embedder=%s model=%s dim=%d",
        type(embedder).__name__,
        getattr(embedder, "model", "-"),
        settings.embed_dim,
    )

    # 지난번에 죽은 태스크가 남긴 running 행을 정리한다. 안 하면 "어제 수집이
    # 아직 도는 중인가?" 를 crawl_run 만 보고 판단할 수 없다.
    # job_timeout 의 2배를 넘긴 것만 건드리므로 살아 있는 실행은 안전하다.
    async with ctx["sessionmaker"]() as session:
        stale = await repository.fail_stale_runs(
            session, older_than_seconds=settings.arq_job_timeout * 2
        )
        await session.commit()
    if stale:
        log.warning("이전 실행에서 마감되지 못한 crawl_run %d건을 failed 로 정리했습니다.", stale)


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engines()
    log.info("worker 종료 — 커넥션 풀 정리 완료")


class WorkerSettings:
    """
    max_tries 는 명세의 재시도 횟수와 1:1 이다.
        crawl_dispatch 1 · crawl_site 3 · embed_postings 3
        embed_backfill 2 · embed_companies 2
    """

    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = get_settings().arq_max_jobs  # 4 — 임베딩 API 동시 호출 제한
    job_timeout = get_settings().arq_job_timeout  # 600

    #   24시간. 중복 큐잉 차단이 여기에 달려 있다.
    #   _job_id = crawl:{site}:{keyword}:{date} 로 같은 날 중복을 막는데,
    #   arq 는 결과가 만료되면 그 job_id 를 "본 적 없는 것" 으로 취급한다.
    #   기본값 3600 이면 1시간 뒤 차단이 풀려서, 워커를 재시작할 때마다
    #   run_at_startup 이 같은 날 수집을 처음부터 다시 돌린다.
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
        cron(crawl_dispatch, hour=4, minute=0, run_at_startup=True, max_tries=1),
        cron(embed_backfill, hour=5, minute=30, max_tries=2),
        cron(embed_companies, hour=6, minute=0, max_tries=2),
    ]
