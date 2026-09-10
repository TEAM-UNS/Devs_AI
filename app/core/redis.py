from datetime import datetime

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def init_redis_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


def get_redis() -> ArqRedis:
    if _pool is None:
        raise RuntimeError(
            "아직 초기화 안됬다. lifespan 해라."
        )
    return _pool


async def close_redis_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


# ── 키 규칙 ─────────────────────────────────────────────────────────────────
def rate_limit_key(user_id: str, now: datetime) -> str:
    return f"rl:{user_id}:{now:%Y%m%d%H%M}"


def token_budget_key(user_id: str, now: datetime) -> str:
    return f"budget:{user_id}:{now:%Y%m%d}"


def crawl_job_id(source: str, keyword: str, day: str) -> str:
    return f"crawl:{source}:{keyword}:{day}"
