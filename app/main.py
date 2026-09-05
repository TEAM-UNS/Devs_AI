from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import close_engine, db_ping
from app.core.exception.handlers import register_exception_handlers
from app.core.redis import close_redis_pool, init_redis_pool
from app.core.redis import ping as redis_ping

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_redis_pool()
    yield
    await close_redis_pool()
    await close_engine()


app = FastAPI(title="jobstack-ai", lifespan=lifespan)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"db": await db_ping(), "redis": await redis_ping()}
