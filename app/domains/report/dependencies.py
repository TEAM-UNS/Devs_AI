from app.core.database import session_factory
from app.domains.report.service import ReportService
from app.infra.llm.factory import build_llm


def build_report_service() -> ReportService:
    return ReportService(
        session_factory,
        build_llm()
    )