import logging
from datetime import date, timedelta
from typing import Any

from app.domains.report.dependencies import build_report_service

log = logging.getLogger(__name__)


async def generate_week(
    ctx: dict[str, Any],
    week_start: str,
    force: bool,
    attempt: int = 1,
) -> dict[str, int]:

    async with ctx["sessionmaker"]() as session:
        service = build_report_service(session)
        counts = await service.generate_week(
            date.fromisoformat(week_start),
            force
        )

    if counts["retryable"]:
        await _retry_later(
            ctx,
            week_start,
            attempt,
            counts["retryable"]
        )

    return counts


async def fill_missing(ctx: dict[str, Any]) -> dict[str, int]:
    async with ctx["sessionmaker"]() as session:
        service = build_report_service(session)

        return await service.fill_missing()


async def _retry_later(
    ctx: dict[str, Any],
    week_start: str,
    attempt: int,
    failed: int,
) -> None:
    settings = ctx["settings"]

    if attempt >= settings.report_retry_attempts:
        log.warning(
            "리포트 %s — %d건 실패. 재시도 %d회 소진, 이후 fill_missing 이 맡는다",
            week_start, failed, attempt,
        )
        return

    delay = timedelta(
        minutes=settings.report_retry_base_minutes * (2 ** (attempt - 1))
    )

    await ctx["redis"].enqueue_job(
        "generate_week",
        week_start,
        False,
        attempt + 1,
        _queue_name=settings.report_queue_name,
        _job_id=f"report:{week_start}:retry:{attempt + 1}",
        _defer_by=delay,
    )

    log.info(
        "리포트 %s — %d건 실패, %s 뒤 재시도 (%d회차)",
        week_start, failed, delay, attempt + 1
    )