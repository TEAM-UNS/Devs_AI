import httpx
import logging
from datetime import date, timedelta
from typing import Any

from arq import Retry

from app.domains.report.schemas import ReportReady
from app.domains.report.dependencies import build_report_service

log = logging.getLogger(__name__)


async def generate_week(
    ctx: dict[str, Any],
    week_start: str,
    force: bool,
    attempt: int = 1,
) -> dict[str, int]:

    service = build_report_service()
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

    await _notify(ctx, week_start, counts)

    return counts


async def _notify(
    ctx: dict[str, Any],
    week_start: str,
    counts: dict[str, int]
):
    settings = ctx["settings"]
    
    if not settings.report_callback_url:
        return

    payload = ReportReady(
        week_start_date=date.fromisoformat(week_start),
        total=counts["total"],
        generated=counts["generated"],
        skipped=counts["skipped"],
        pending=counts["retryable"] + counts["blocked"],
    )

    await ctx["redis"].enqueue_job(
        "notify_backend",
        payload.model_dump(mode="json"),
        _queue_name=settings.report_queue_name,
    )


async def notify_backend(ctx: dict[str, Any], payload: dict[str, Any]) -> int:
    settings = ctx["settings"]
    try:
        async with httpx.AsyncClient(timeout=settings.report_callback_timeout) as client:
            response = await client.post(settings.report_callback_url, json=payload)
            response.raise_for_status()

    except Exception as exc:
        job_try = ctx["job_try"]

        if job_try < settings.report_callback_attempts:
            log.warning(
                "백엔드 통지 실패 (%d회차) %s — 다시 보냅니다", job_try, exc
            )
            raise Retry(defer=settings.report_callback_base_seconds * 2 ** (job_try - 1))

        log.error("백엔드 통지 포기 (%d회차) %s", job_try, exc)
        raise

    log.info("백엔드 통지 완료 %s", payload["week_start_date"])

    return response.status_code


async def fill_missing(ctx: dict[str, Any]) -> dict[str, int]:
    return await build_report_service().fill_missing()


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