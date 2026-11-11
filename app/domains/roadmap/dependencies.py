from app.core.database import session_factory
from app.domains.roadmap.service import RoadmapService
from app.infra.llm.factory import build_llm


def build_report_service() -> RoadmapService:
    return RoadmapService(
        session_factory,
        build_llm()
    )