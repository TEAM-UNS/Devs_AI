from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dependencies import SessionDep
from app.domains.report.repository import ReportRepository
from app.domains.report.service import ReportService
from app.infra.llm.client import build_chat_model


def get_report_repository(session: SessionDep) -> ReportRepository:
    return ReportRepository(session)

ReportRepositoryDep = Annotated[
    ReportRepository,
    Depends(get_report_repository)
]


def build_report_service(session: AsyncSession) -> ReportService:
    return ReportService(
        ReportRepository(session),
        build_chat_model()
    )