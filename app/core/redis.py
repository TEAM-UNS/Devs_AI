"""arq redis pool.

- 풀은 앱/워커 lifespan 에서 1회 만들고 재사용한다 (요청마다 만들지 않는다).
- 라우터/서비스는 이 풀로 태스크를 enqueue 한다.
- 챗봇 레이트리밋 카운터도 같은 redis 를 쓴다. 키 규칙은 아래 헬퍼로 통일.

ArqRedis 는 redis.asyncio.Redis 의 서브클래스라 일반 명령(incr · expire 등)도
그대로 쓸 수 있다.
"""

from __future__ import annotations

from datetime import datetime

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    """arq WorkerSettings.redis_settings 에도 그대로 쓴다."""
    return RedisSettings.from_dsn(get_settings().redis_url)


async def init_redis_pool() -> ArqRedis:
    """lifespan 시작 시 1회 호출. 이미 있으면 그대로 반환한다."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


def get_redis() -> ArqRedis:
    """초기화된 풀을 반환한다. lifespan 밖에서 부르면 즉시 실패시킨다."""
    if _pool is None:
        raise RuntimeError(
            "redis pool 이 초기화되지 않았습니다. lifespan 에서 init_redis_pool() 을 먼저 호출하세요."
        )
    return _pool


async def close_redis_pool() -> None:
    """lifespan 종료 시 호출."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def ping() -> bool:
    """헬스체크용."""
    try:
        return bool(await get_redis().ping())
    except Exception:  # noqa: BLE001 — 헬스체크는 예외를 밖으로 내보내지 않는다
        return False


# ── 키 규칙 ─────────────────────────────────────────────────────────────────
# guard.py 가 사용한다. 키 문자열을 여기 한 곳에서만 만든다.
def rate_limit_key(user_id: str, now: datetime) -> str:
    """분당 요청수 카운터. 분 단위 버킷이라 TTL 60초면 충분하다."""
    return f"rl:{user_id}:{now:%Y%m%d%H%M}"


def token_budget_key(user_id: str, now: datetime) -> str:
    """일일 토큰 예산 카운터."""
    return f"budget:{user_id}:{now:%Y%m%d}"


def crawl_job_id(source: str, keyword: str, day: str) -> str:
    """arq 중복 큐잉 차단용 job id. 같은 날 같은 조합은 1번만 실행된다."""
    return f"crawl:{source}:{keyword}:{day}"
