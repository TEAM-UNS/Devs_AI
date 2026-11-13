from datetime import datetime

from fastapi import APIRouter, status

from app.core.config import get_settings
from app.core.dependencies import RedisDep
from app.domains.report.schemas import (
    GenerateAccepted,
    GenerateRequest
)


settings = get_settings()

report_router = APIRouter(prefix="/api/reports", tags=["report"])


@report_router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED
)
async def generate(
    body: GenerateRequest,
    redis: RedisDep,
) -> GenerateAccepted:
    week = body.week_start_date.isoformat()
    stamp = datetime.now().strftime("%Y%m%d%H%M")

    job = await redis.enqueue_job(
        "generate_week",
        week,
        body.force,
        _queue_name=settings.report_queue_name,
        _job_id=f"report:{week}:{stamp}",
    )

    return GenerateAccepted(
        week_start_date=body.week_start_date,
        queued=job is not None,
    )