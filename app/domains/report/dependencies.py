from app.core.database import session_factory
from app.domains.report.service import ReportService
from app.infra.llm.client import build_chat_model


def build_report_service() -> ReportService:
    return ReportService(
        session_factory,
        build_chat_model()
    )