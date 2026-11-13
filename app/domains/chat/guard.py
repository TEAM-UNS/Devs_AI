from datetime import (
    datetime,
    timedelta,
)
from zoneinfo import ZoneInfo

from arq.connections import ArqRedis

from app.core.config import get_settings
from app.core.redis import (
    rate_limit_key,
    token_budget_key,
)
from app.domains.chat.exceptions import RateLimited


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def _midnight(now: datetime) -> datetime:
    return (
        (now + timedelta(days=1))
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
    )


async def check(redis: ArqRedis, user_id: int) -> dict[str, str]:
    settings = get_settings()
    now = _now()

    budget = settings.chat_daily_token_budget
    if budget > 0:
        spent = await redis.incrby(
            token_budget_key(user_id, now), 0
        )
        if spent >= budget:
            raise RateLimited(
                "오늘 사용량을 모두 썼습니다. 내일 다시 이용해주세요.",
                reset=int(_midnight(now).timestamp()),
            )

    limit = settings.chat_rate_limit_per_minute
    if limit <= 0:
        return {}

    key = rate_limit_key(user_id, now)
    used = await redis.incr(key)
    if used == 1:
        await redis.expire(key, 60)

    reset = int(
        (now.replace(second=0, microsecond=0)
         + timedelta(minutes=1)).timestamp()
    )
    if used > limit:
        raise RateLimited(
            "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.",
            reset=reset
        )

    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(limit - used),
        "X-RateLimit-Reset": str(reset),
    }


async def add_tokens(redis: ArqRedis, user_id: int, tokens: int):
    if tokens <= 0 or get_settings().chat_daily_token_budget <= 0:
        return

    now = _now()
    key = token_budget_key(user_id, now)

    spent = await redis.incrby(key, tokens)
    if spent == tokens:
        await redis.expire(
            key,
            int(
                (_midnight(now) - now)
                .total_seconds()
            )
        )