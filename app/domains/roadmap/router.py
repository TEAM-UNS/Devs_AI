from datetime import datetime

from fastapi import APIRouter, status

from app.core.config import get_settings
from app.core.dependencies import RedisDep
from app.domains.roadmap.schemas import (
    RoadmapGenerateRequest,
)


settings = get_settings()

roadmap_router = APIRouter(prefix="/api/roadmaps", tags=["roadmap"])


@roadmap_router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED
)
async def generate(
    body: RoadmapGenerateRequest,
    redis: RedisDep,
):
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")

    await redis.enqueue_job(
        "generate_roadmap",
        body.roadmap_id,
        body.additional_comment,
        _queue_name=settings.roadmap_queue_name,
        _job_id=f"roadmap:{body.roadmap_id}:{stamp}",
    )

    return {"message": "accepted"}