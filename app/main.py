import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import close_engine, db_ping
from app.core.exception.handlers import register_exception_handlers
from app.core.middleware import register_middleware
from app.core.redis import close_redis_pool, init_redis_pool
from app.core.redis import ping as redis_ping

from app.domains.chat.router import chat_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_redis_pool()
    yield
    await close_redis_pool()
    await close_engine()


app = FastAPI(title="jobstack-ai", lifespan=lifespan)

register_exception_handlers(app)
register_middleware(app)

app.include_router(chat_router)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {
        "db": await db_ping(),
        "redis": await redis_ping()
    }
