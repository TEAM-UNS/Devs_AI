from app.core.database import session_factory
from app.domains.roadmap.service import RoadmapService
from app.infra.llm.client import build_chat_model


def build_roadmap_service() -> RoadmapService:
    return RoadmapService(
        session_factory,
        build_chat_model()
    )