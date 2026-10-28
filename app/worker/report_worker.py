import logging
from typing import Any, ClassVar

from arq import cron
from arq.worker import func

from app.core.config import get_settings
from app.core.database import (
    close_engine, engine,
    session_factory
)
from app.core.log import setup_logging
from app.core.redis import redis_settings
from app.infra.llm.tracing import shutdown_tracing
from app.domains.report.task import (
    fill_missing,
    generate_week,
    notify_backend,
)


log = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]):
    setup_logging()
    settings = get_settings()

    ctx["engine"] = engine
    ctx["settings"] = settings
    ctx["sessionmaker"] = session_factory

    log.info(
        "report worker 기동 — queue=%s",
        settings.report_queue_name
    )


async def shutdown(ctx: dict[str, Any]):
    await close_engine()
    shutdown_tracing()
    log.info("report worker 종료")


class ReportWorkerSettings:
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    queue_name = get_settings().report_queue_name
    job_timeout = get_settings().report_job_timeout

    max_jobs = 1
    keep_result = 86400

    max_tries = 3

    functions: ClassVar[list] = [
        func(generate_week, name="generate_week", max_tries=3),
        func(fill_missing, name="fill_missing", max_tries=1),
        func(notify_backend, name="notify_backend", max_tries=5),
    ]

    cron_jobs: ClassVar[list] = [
        cron(fill_missing, minute=0, max_tries=1),
    ]