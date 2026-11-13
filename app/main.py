import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.log import setup_logging
from app.core.config import get_settings
from app.core.database import close_engine, db_ping
from app.core.exception.handlers import register_exception_handlers
from app.core.redis import close_redis_pool, init_redis_pool
from app.core.redis import ping as redis_ping
from app.infra.decision.jev import close_jev
from app.infra.llm.tracing import shutdown_tracing

from app.domains.chat.router import chat_router
from app.domains.report.router import report_router
from app.domains.roadmap.router import roadmap_router


setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_redis_pool()
    yield
    await close_redis_pool()
    await close_engine()
    await close_jev()
    shutdown_tracing()


app = FastAPI(title="jobstack-ai", lifespan=lifespan)

register_exception_handlers(app)

app.include_router(chat_router)
app.include_router(report_router)
app.include_router(roadmap_router)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {
        "db": await db_ping(),
        "redis": await redis_ping()
    }
