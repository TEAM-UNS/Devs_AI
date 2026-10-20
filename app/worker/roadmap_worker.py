import logging
from typing import Any, ClassVar

from arq.worker import func

from app.core.config import get_settings
from app.core.database import (
    close_engine, engine,
    session_factory
)
from app.core.log import setup_logging
from app.core.redis import redis_settings

# from app.domains.roadmap.task import (
# 
# )

log = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]):
    setup_logging()
    settings = get_settings()

    ctx["engine"] = engine
    ctx["settings"] = settings
    ctx["sessionmaker"] = session_factory

    log.info(
        "roadmap worker 기동 — queue=%s",
        settings.roadmap_queue_name
    )


async def shutdown(ctx: dict[str, Any]):
    await close_engine()
    log.info("roadmap worker 종료")


class RoadmapWorkerSettings:
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    queue_name = get_settings().roadmap_queue_name
    job_timeout = get_settings().roadmap_job_timeout

    max_jobs = 1
    keep_result = 86400

    max_tries = 3

