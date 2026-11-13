import logging
from typing import Any, Optional

from app.domains.roadmap.dependencies import build_roadmap_service

log = logging.getLogger(__name__)


async def generate_roadmap(
    ctx: dict[str, Any],
    roadmap_id: int,
    additional_comment: Optional[str] = None,
) -> dict[str, Any]:
    return await build_roadmap_service().roadmap_generator(
        roadmap_id,
        additional_comment,
    )


async def fail_stale_roadmaps(ctx: dict[str, Any]) -> int:
    return await build_roadmap_service().fail_stale(
        ctx["settings"].roadmap_stale_seconds
    )